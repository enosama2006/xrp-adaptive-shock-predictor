"""Extended-horizon Model B runtime with independent per-horizon promotion.

Each horizon is trained, gated, versioned, and served independently. A valid
8-hour model can therefore operate while a rare 15-minute ±10% model remains
WAIT. No missing horizon is filled with fabricated probabilities.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import time
from typing import Any

import joblib
import pandas as pd

from .anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetConfig, AnchorDatasetStore
from .baseline import BaselineConfig
from .dataset_state import DatasetStateStore
from .fast_anchor_dataset import _initial_dataset, _normalize_candles, _save_and_advance_state
from .feature_registry import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from .features import join_anchors_with_features
from .first_touch_v4 import FIRST_TOUCH_GATE_VERSION, train_first_touch_v4
from .horizons import RESEARCH_HORIZONS_MINUTES, RESEARCH_HORIZON_SET_VERSION
from .labeling import CandlePoint
from .pipeline import IncrementalResearchPipeline, PipelineConfig, PipelinePaths
from .platform_runtime import RealDataPlatform, RuntimeConfig, RuntimePaths
from .prediction_ledger import PredictionRecord

HORIZONS = RESEARCH_HORIZONS_MINUTES
LABELS = ("UP_10", "DOWN_10", "NO_EVENT")
ANCHOR_REBUILD_CHUNK_ROWS = 10_000


def _bundle_model_keys(bundle: dict[str, Any]) -> set[int]:
    return {int(value) for value in bundle.get("models", {})}


def _valid_bundle(bundle: Any) -> bool:
    if not isinstance(bundle, dict):
        return False
    if bundle.get("gate_methodology_version") != FIRST_TOUCH_GATE_VERSION:
        return False
    if bundle.get("horizon_set_version") != RESEARCH_HORIZON_SET_VERSION:
        return False
    keys = _bundle_model_keys(bundle)
    return bool(keys) and keys.issubset(set(HORIZONS))


def _anchor_horizon_matrix_complete(frame: pd.DataFrame) -> bool:
    """Require one row for every configured horizon at every anchor timestamp."""

    if frame.empty:
        return False
    expected = set(HORIZONS)
    actual = {int(value) for value in frame["horizon_minutes"].dropna().unique()}
    if actual != expected:
        return False
    anchor_count = int(frame["anchor_timestamp_ms"].nunique())
    if anchor_count <= 0:
        return False
    counts = (
        frame.groupby("horizon_minutes")["anchor_timestamp_ms"]
        .nunique()
        .to_dict()
    )
    return all(int(counts.get(horizon, 0)) == anchor_count for horizon in HORIZONS)


class ExtendedHorizonRealDataPlatform(RealDataPlatform):
    """Real-data platform with eight governed cumulative horizons."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        super().__init__(paths, config)
        self.pipeline = IncrementalResearchPipeline(
            PipelinePaths(paths.prices, paths.anchors, paths.state),
            PipelineConfig(
                symbol=config.symbol,
                bootstrap_start_ms=config.bootstrap_start_ms,
                checkpoint_rows=config.checkpoint_rows,
                anchor_config=AnchorDatasetConfig(horizons_minutes=HORIZONS),
            ),
        )
        self.price_store = self.pipeline.price_store
        self._bundle = None
        self._load_extended_bundle()

    def _load_extended_bundle(self) -> None:
        if self.paths.models.exists():
            loaded = joblib.load(self.paths.models)
            if _valid_bundle(loaded):
                self._bundle = loaded
                self.status.model_available = True
                self.status.model_version = str(loaded.get("model_version"))
                self.status.last_training_final_rows = int(
                    loaded.get("training_final_rows", 0)
                )
                return
        self.status.model_available = False
        self.status.model_version = None
        self.status.last_training_final_rows = 0
        if self.paths.models.exists():
            self.status.state = "WAIT"
            self.status.reason = "legacy_first_touch_gate_or_horizon_set_invalidated"
            self._save_status()

    def _backup_anchor_dataset(self) -> Path | None:
        if not self.paths.anchors.exists():
            return None
        backup = self.paths.anchors.with_name(
            f"{self.paths.anchors.stem}.before-{RESEARCH_HORIZON_SET_VERSION}.parquet"
        )
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.paths.anchors, backup)
        return backup

    def _ensure_extended_anchor_horizons(self) -> pd.DataFrame:
        store = AnchorDatasetStore(self.paths.anchors)
        existing = store.load()
        if _anchor_horizon_matrix_complete(existing):
            return existing

        self._set_lifecycle(
            "MIGRATE_HORIZONS",
            progress=0.0,
            message="rebuilding_historical_anchors_for_extended_horizons",
        )
        self._backup_anchor_dataset()
        prices = self._load_prices()
        candles = [
            CandlePoint(
                timestamp_ms=int(row.timestamp_ms),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.price),
            )
            for row in prices.itertuples(index=False)
        ]
        points = _normalize_candles(candles)
        if not points:
            empty = pd.DataFrame(columns=ANCHOR_COLUMNS)
            store.save(empty)
            return empty
        config = AnchorDatasetConfig(horizons_minutes=HORIZONS)
        rebuilt = _initial_dataset(
            points,
            config,
            chunk_rows=ANCHOR_REBUILD_CHUNK_ROWS,
        )
        result = _save_and_advance_state(
            rebuilt,
            store=store,
            state_store=DatasetStateStore(self.paths.state),
            latest_timestamp_ms=points[-1].timestamp_ms,
        )
        if not _anchor_horizon_matrix_complete(result):
            raise RuntimeError("extended anchor horizon matrix rebuild is incomplete")
        self.status.anchor_rows = int(len(result))
        self.status.final_rows = int((result["status"] == "FINAL").sum())
        self.status.pending_rows = int((result["status"] == "PENDING").sum())
        self._set_lifecycle(
            "MIGRATE_HORIZONS",
            progress=1.0,
            message="extended_historical_horizons_ready",
        )
        return result

    def sync_real_data(self, end_ms: int | None = None) -> None:
        super().sync_real_data(end_ms)
        anchors = self._ensure_extended_anchor_horizons()
        self.status.anchor_rows = int(len(anchors))
        self.status.final_rows = int((anchors["status"] == "FINAL").sum())
        self.status.pending_rows = int((anchors["status"] == "PENDING").sum())
        self._save_status()

    @staticmethod
    def _failure_reason(reports: dict[str, Any]) -> str:
        reasons = {
            str(report.get("reason", "unknown"))
            for report in reports.values()
            if isinstance(report, dict)
        }
        if "insufficient_independent_directional_events_across_untouched_periods" in reasons:
            return "model_b_independent_walk_forward_event_support_wait"
        if "walk_forward_split_unavailable" in reasons:
            return "model_b_walk_forward_split_wait"
        if "directional_empirical_precision_below_required_85pct" in reasons:
            return "model_b_directional_precision_gate_wait"
        return "directional_event_evidence_gate_failed"

    def train_if_due(self, force: bool = False) -> bool:
        if not self.config.training_enabled:
            return False
        anchors = AnchorDatasetStore(self.paths.anchors).load()
        features = pd.read_parquet(self.paths.features)
        final_count = int((anchors["status"] == "FINAL").sum())
        due = force or (
            final_count
            >= self.status.last_training_final_rows
            + self.config.retrain_after_new_final_rows
        )
        if not due:
            return False

        self._set_lifecycle(
            "TRAIN_MODEL_B",
            progress=0.0,
            message="training_first_touch_independent_horizon_challengers",
        )
        self._save_feature_diagnostics(features)
        matrix = join_anchors_with_features(anchors, features)
        feature_names = self._feature_names(features)
        incumbent_models: dict[int, Any] = {}
        if self._bundle is not None:
            incumbent_models = {
                int(horizon): model
                for horizon, model in self._bundle.get("models", {}).items()
            }
        models = dict(incumbent_models)
        reports: dict[str, Any] = {}
        promoted_horizons: list[int] = []
        rejected_horizons: list[int] = []

        for index, horizon in enumerate(HORIZONS, start=1):
            subset = matrix[matrix["horizon_minutes"] == horizon].copy()
            horizon_ms = horizon * 60_000
            model, report = train_first_touch_v4(
                subset,
                feature_names,
                BaselineConfig(
                    minimum_rows=self.config.minimum_final_rows_per_horizon,
                    label_horizon_ms=horizon_ms,
                    embargo_ms=horizon_ms,
                ),
            )
            reports[str(horizon)] = asdict(report)
            if model is None:
                rejected_horizons.append(horizon)
            else:
                models[horizon] = model
                promoted_horizons.append(horizon)
            self._set_lifecycle(
                "TRAIN_MODEL_B",
                progress=index / len(HORIZONS),
                message=f"trained_or_evaluated_horizon_{horizon}m",
            )

        self.paths.reports.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.paths.reports.with_suffix(".json.tmp")
        temporary_report.write_text(
            json.dumps(reports, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_report.replace(self.paths.reports)
        self.status.last_training_final_rows = final_count

        if not models:
            self._bundle = None
            self.status.model_available = False
            self.status.model_version = None
            self.status.state = "WAIT"
            self.status.reason = self._failure_reason(reports)
            self._set_lifecycle(
                "MODEL_B_WAIT",
                progress=1.0,
                message="model_b_all_horizons_wait",
            )
            return False

        version = f"real-logistic-independent-horizons-{int(time.time())}"
        bundle = {
            "model_version": version,
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "configured_horizons": list(HORIZONS),
            "available_horizons": sorted(models),
            "promoted_horizons_this_run": promoted_horizons,
            "rejected_horizons_this_run": rejected_horizons,
            "models": models,
            "reports": reports,
            "training_final_rows": final_count,
            "source": "real_binance_public_data_only",
            "promotion_evidence": (
                "independent_event_clusters_plus_purged_walk_forward_support_"
                "plus_untouched_temporal_precision"
            ),
            "promoted_for_trading": False,
        }
        self.paths.models.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.models.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        temporary.replace(self.paths.models)
        self._bundle = bundle
        self.status.model_available = True
        self.status.model_version = version
        self.status.state = "PARTIAL_RESEARCH"
        self.status.reason = (
            "model_b_all_horizons_research_ready"
            if set(models) == set(HORIZONS)
            else "model_b_some_horizons_research_ready_others_wait"
        )
        self._set_lifecycle(
            "MODEL_B_RESEARCH_READY",
            progress=1.0,
            message="model_b_independent_horizon_gates_evaluated",
        )
        return bool(promoted_horizons)

    def predict_latest(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if self._bundle is None:
            self.status.state = "WAIT"
            self.status.reason = "no_directionally_valid_first_touch_horizon"
            self._save_status()
            return []
        if (
            self.status.last_prediction_ms is not None
            and timestamp - self.status.last_prediction_ms
            < self.config.prediction_cadence_ms
        ):
            return []

        self._set_lifecycle(
            "PREDICT",
            progress=0.0,
            message="creating_model_b_predictions_for_available_horizons",
        )
        features = pd.read_parquet(self.paths.features)
        if features.empty:
            return []
        latest = features.sort_values("timestamp_ms").iloc[-1]
        anchor_ms = int(latest["timestamp_ms"])
        anchor_price = float(latest["price"])
        feature_names = list(self._bundle["feature_names"])
        row = pd.DataFrame([{name: latest.get(name) for name in feature_names}])
        records: list[PredictionRecord] = []
        output: list[dict[str, Any]] = []
        models = {
            int(horizon): model
            for horizon, model in self._bundle.get("models", {}).items()
        }
        for horizon in sorted(models):
            model = models[horizon]
            probabilities = model.predict_proba(row)[0]
            classes = [str(value) for value in model.classes_]
            mapped = {label: 0.0 for label in LABELS}
            for index, label in enumerate(classes):
                if label in mapped:
                    mapped[label] = float(probabilities[index])
            total = sum(mapped.values())
            if total <= 0:
                continue
            mapped = {key: value / total for key, value in mapped.items()}
            record = PredictionRecord(
                created_at_ms=timestamp,
                anchor_timestamp_ms=anchor_ms,
                anchor_price=anchor_price,
                horizon_minutes=horizon,
                model_version=str(self._bundle["model_version"]),
                dataset_id=f"real-partitioned-{RESEARCH_HORIZON_SET_VERSION}",
                feature_schema_version=str(
                    self._bundle.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
                ),
                p_up_10=mapped["UP_10"],
                p_down_10=mapped["DOWN_10"],
                p_no_event=mapped["NO_EVENT"],
                decision="WAIT",
                decision_reason="research_model_not_promoted_for_trading",
            )
            records.append(record)
            output.append(record.to_row())
        if records:
            self.ledger.append(records)
            self.status.last_prediction_ms = timestamp
            self.status.updated_at_ms = timestamp
            self._set_lifecycle(
                "PREDICTIONS_STORED",
                progress=1.0,
                message="model_b_available_horizon_predictions_stored",
            )
        return output


__all__ = [
    "ExtendedHorizonRealDataPlatform",
    "HORIZONS",
]
