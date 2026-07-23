"""Real-data runtime for incremental collection, training, and governed predictions.

No synthetic rows or heuristic probabilities are permitted. When real data or a
validated fitted model is unavailable, the runtime emits WAIT with an explicit reason.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .baseline import (
    FIRST_TOUCH_GATE_VERSION,
    BaselineConfig,
    train_multinomial_baseline,
)
from .feature_registry import (
    SCHEMA_VERSION as FEATURE_SCHEMA_VERSION,
)
from .feature_registry import (
    audit_feature_columns,
    select_model_feature_names,
)
from .features import build_feature_diagnostics, build_price_features, join_anchors_with_features
from .labeling import CandlePoint
from .pipeline import (
    IncrementalResearchPipeline,
    PipelineConfig,
    PipelinePaths,
    PipelineProgress,
)
from .prediction_ledger import PredictionLedger, PredictionRecord

HORIZONS = (15, 30, 45, 60)
LABELS = ("UP_10", "DOWN_10", "NO_EVENT")


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    prices: Path = Path("data/prices.parquet")
    anchors: Path = Path("data/anchors.parquet")
    features: Path = Path("data/features.parquet")
    state: Path = Path("data/state.json")
    models: Path = Path("models/champion.joblib")
    reports: Path = Path("reports/training.json")
    feature_diagnostics: Path = Path("reports/feature_diagnostics.json")
    ledger: Path = Path("data/predictions.parquet")
    status: Path = Path("data/platform_status.json")


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    bootstrap_start_ms: int
    symbol: str = "XRPUSDT"
    minimum_final_rows_per_horizon: int = 2_000
    retrain_after_new_final_rows: int = 500
    prediction_cadence_ms: int = 60_000
    training_enabled: bool = True
    checkpoint_rows: int = 10_000


@dataclass(slots=True)
class PlatformStatus:
    updated_at_ms: int
    state: str
    reason: str
    price_rows: int
    anchor_rows: int
    final_rows: int
    pending_rows: int
    model_available: bool
    model_version: str | None
    last_prediction_ms: int | None
    last_training_final_rows: int
    data_start_ms: int | None
    data_end_ms: int | None
    lifecycle_stage: str = "IDLE"
    lifecycle_progress: float = 0.0
    lifecycle_message: str = "not_started"
    cycle_started_at_ms: int | None = None
    last_successful_cycle_ms: int | None = None
    requested_start_ms: int | None = None
    requested_end_ms: int | None = None
    expected_rows: int = 0
    processed_rows: int = 0
    checkpoint_writes: int = 0
    current_watermark_ms: int | None = None
    price_partition_count: int = 0


class RealDataPlatform:
    """One-process research runtime backed exclusively by public exchange data."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        self.paths = paths
        self.config = config
        self.pipeline = IncrementalResearchPipeline(
            PipelinePaths(paths.prices, paths.anchors, paths.state),
            PipelineConfig(
                symbol=config.symbol,
                bootstrap_start_ms=config.bootstrap_start_ms,
                checkpoint_rows=config.checkpoint_rows,
            ),
        )
        self.price_store = self.pipeline.price_store
        self.ledger = PredictionLedger(paths.ledger)
        self._bundle: dict[str, Any] | None = None
        self._status = self._load_status()
        self._active_collection_stage = "SYNC_MISSING_TAIL"
        if paths.models.exists():
            loaded = joblib.load(paths.models)
            if (
                isinstance(loaded, dict)
                and loaded.get("gate_methodology_version") == FIRST_TOUCH_GATE_VERSION
            ):
                self._bundle = loaded
                self._status.model_available = True
                self._status.model_version = str(loaded.get("model_version"))
                self._status.last_training_final_rows = int(loaded.get("training_final_rows", 0))
            else:
                # Earlier bundles did not require enough directional evidence across
                # multiple purged untouched periods. They are not v3 champions.
                self._status.model_available = False
                self._status.model_version = None
                self._status.last_training_final_rows = 0
                self._status.state = "WAIT"
                self._status.reason = "legacy_first_touch_gate_invalidated"
                self._save_status()

    @staticmethod
    def _new_status() -> PlatformStatus:
        return PlatformStatus(
            updated_at_ms=int(time.time() * 1000),
            state="WAIT",
            reason="not_started",
            price_rows=0,
            anchor_rows=0,
            final_rows=0,
            pending_rows=0,
            model_available=False,
            model_version=None,
            last_prediction_ms=None,
            last_training_final_rows=0,
            data_start_ms=None,
            data_end_ms=None,
        )

    def _load_status(self) -> PlatformStatus:
        default = self._new_status()
        if not self.paths.status.exists():
            return default
        payload = json.loads(self.paths.status.read_text(encoding="utf-8"))
        merged = asdict(default)
        for name in merged:
            if name in payload:
                merged[name] = payload[name]
        return PlatformStatus(**merged)

    def _save_status(self) -> None:
        self.paths.status.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.status.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(self._status), indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.paths.status)

    def _set_lifecycle(
        self,
        stage: str,
        *,
        progress: float | None = None,
        message: str | None = None,
        save: bool = True,
    ) -> None:
        self._status.lifecycle_stage = stage
        if progress is not None:
            self._status.lifecycle_progress = min(1.0, max(0.0, float(progress)))
        if message is not None:
            self._status.lifecycle_message = message
        self._status.updated_at_ms = int(time.time() * 1000)
        if save:
            self._save_status()

    def _on_pipeline_progress(self, progress: PipelineProgress) -> None:
        stage = (
            self._active_collection_stage if progress.stage == "COLLECT_HISTORY" else progress.stage
        )
        self._status.lifecycle_stage = stage
        self._status.lifecycle_progress = progress.progress_fraction
        self._status.lifecycle_message = progress.stage.lower()
        self._status.requested_start_ms = progress.requested_start_ms
        self._status.requested_end_ms = progress.requested_end_ms
        self._status.expected_rows = progress.expected_rows
        self._status.processed_rows = progress.processed_rows
        self._status.checkpoint_writes = progress.checkpoint_writes
        self._status.current_watermark_ms = progress.current_watermark_ms
        self._status.price_rows = progress.total_price_rows
        self._status.updated_at_ms = int(time.time() * 1000)
        self._save_status()

    def _load_prices(self) -> pd.DataFrame:
        frame = self.price_store.load()
        required = {"timestamp_ms", "price", "open", "high", "low"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"price dataset missing columns: {sorted(missing)}")
        columns = [
            name
            for name in (
                "timestamp_ms",
                "price",
                "open",
                "high",
                "low",
                "volume",
                "quote_volume",
                "trade_count",
                "taker_buy_base",
                "taker_buy_quote",
            )
            if name in frame.columns
        ]
        return (
            frame[columns]
            .drop_duplicates("timestamp_ms", keep="last")
            .sort_values("timestamp_ms", ignore_index=True)
        )

    @staticmethod
    def _feature_names(features: pd.DataFrame) -> list[str]:
        return select_model_feature_names(features)

    def sync_real_data(self, end_ms: int | None = None) -> None:
        cutoff = int(time.time() * 1000) if end_ms is None else end_ms
        fresh_bootstrap = not self.price_store.exists or self._status.price_rows == 0
        self._active_collection_stage = (
            "BOOTSTRAP_HISTORY" if fresh_bootstrap else "SYNC_MISSING_TAIL"
        )
        self._status.cycle_started_at_ms = int(time.time() * 1000)
        if self._bundle is None:
            self._status.state = "WAIT"
            self._status.reason = "data_collection_in_progress"
        else:
            self._status.state = "RESEARCH_ONLY"
            self._status.reason = "data_sync_in_progress_existing_champion_loaded"
        self._set_lifecycle(
            self._active_collection_stage,
            progress=0.0,
            message="collecting_completed_market_candles",
        )

        result = self.pipeline.run(cutoff, progress_callback=self._on_pipeline_progress)
        self._status.checkpoint_writes = result.checkpoint_writes
        self._status.price_partition_count = result.price_partition_count
        self._set_lifecycle(
            "BUILD_FEATURES",
            progress=0.0,
            message="building_causal_feature_matrix",
        )
        prices = self._load_prices()
        features = build_price_features(prices)
        self.paths.features.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.features.with_suffix(".parquet.tmp")
        features.to_parquet(temporary, index=False)
        temporary.replace(self.paths.features)

        anchor_stats = AnchorDatasetStore(self.paths.anchors).stats()
        self._status.price_rows = len(prices)
        self._status.anchor_rows = anchor_stats.total_rows
        self._status.final_rows = anchor_stats.final_rows
        self._status.pending_rows = anchor_stats.pending_rows
        self._status.data_start_ms = None if prices.empty else int(prices["timestamp_ms"].min())
        self._status.data_end_ms = None if prices.empty else int(prices["timestamp_ms"].max())
        self._status.current_watermark_ms = self._status.data_end_ms
        self._status.updated_at_ms = cutoff
        if self._bundle is None:
            self._status.state = "WAIT"
            self._status.reason = "real_data_synced_directional_model_gate_pending"
        else:
            self._status.state = "RESEARCH_ONLY"
            self._status.reason = "model_b_champion_loaded_data_synced"
        self._set_lifecycle(
            "DATA_READY",
            progress=1.0,
            message="real_data_and_features_ready_for_model_gates",
        )

    def _save_feature_diagnostics(self, features: pd.DataFrame) -> None:
        report = build_feature_diagnostics(features)
        report["generated_at_ms"] = int(time.time() * 1000)
        report["feature_schema_version"] = FEATURE_SCHEMA_VERSION
        report["selection_audit"] = audit_feature_columns(features).to_dict()
        self.paths.feature_diagnostics.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.feature_diagnostics.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.paths.feature_diagnostics)

    @staticmethod
    def _failure_reason(reports: dict[str, Any]) -> str:
        reasons = {
            str(report.get("reason", "unknown"))
            for report in reports.values()
            if isinstance(report, dict)
        }
        if "insufficient_directional_support_across_untouched_periods" in reasons:
            return "model_b_walk_forward_directional_support_wait"
        if "walk_forward_split_unavailable" in reasons:
            return "model_b_walk_forward_split_wait"
        return "directional_event_evidence_gate_failed"

    def train_if_due(self, force: bool = False) -> bool:
        if not self.config.training_enabled:
            return False
        anchors = AnchorDatasetStore(self.paths.anchors).load()
        features = pd.read_parquet(self.paths.features)
        final_count = int((anchors["status"] == "FINAL").sum())
        due = force or (
            final_count
            >= self._status.last_training_final_rows + self.config.retrain_after_new_final_rows
        )
        if not due:
            return False

        self._set_lifecycle(
            "TRAIN_MODEL_B",
            progress=0.0,
            message="training_first_touch_directional_challenger",
        )
        self._save_feature_diagnostics(features)
        matrix = join_anchors_with_features(anchors, features)
        feature_names = self._feature_names(features)
        models: dict[int, Any] = {}
        reports: dict[str, Any] = {}
        all_ready = True
        for index, horizon in enumerate(HORIZONS, start=1):
            subset = matrix[matrix["horizon_minutes"] == horizon].copy()
            horizon_ms = horizon * 60_000
            model, report = train_multinomial_baseline(
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
                all_ready = False
            else:
                models[horizon] = model
            self._set_lifecycle(
                "TRAIN_MODEL_B",
                progress=index / len(HORIZONS),
                message=f"trained_or_evaluated_horizon_{horizon}m",
            )

        self.paths.reports.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.paths.reports.with_suffix(".json.tmp")
        temporary_report.write_text(json.dumps(reports, indent=2, sort_keys=True), encoding="utf-8")
        temporary_report.replace(self.paths.reports)
        if not all_ready or len(models) != len(HORIZONS):
            self._status.last_training_final_rows = final_count
            failure_reason = self._failure_reason(reports)
            if self._bundle is None:
                self._status.model_available = False
                self._status.model_version = None
                self._status.state = "WAIT"
                self._status.reason = failure_reason
                message = "model_b_directional_event_gate_failed_or_insufficient"
            else:
                self._status.state = "RESEARCH_ONLY"
                self._status.reason = "challenger_rejected_existing_champion_retained"
                message = "model_b_challenger_rejected_champion_retained"
            self._set_lifecycle("MODEL_B_WAIT", progress=1.0, message=message)
            return False

        version = f"real-logistic-walk-forward-{int(time.time())}"
        bundle = {
            "model_version": version,
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "models": models,
            "reports": reports,
            "training_final_rows": final_count,
            "source": "real_binance_public_data_only",
            "promotion_evidence": "purged_walk_forward_support_plus_untouched_temporal_test",
            "promoted_for_trading": False,
        }
        self.paths.models.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.models.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        temporary.replace(self.paths.models)
        self._bundle = bundle
        self._status.model_available = True
        self._status.model_version = version
        self._status.last_training_final_rows = final_count
        self._status.state = "RESEARCH_ONLY"
        self._status.reason = "directional_event_gate_passed_not_trading_promoted"
        self._set_lifecycle(
            "MODEL_B_RESEARCH_READY",
            progress=1.0,
            message="model_b_directional_event_gate_passed",
        )
        return True

    def predict_latest(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if self._bundle is None:
            self._status.state = "WAIT"
            self._status.reason = "no_directionally_valid_first_touch_model"
            self._save_status()
            return []
        if (
            self._status.last_prediction_ms is not None
            and timestamp - self._status.last_prediction_ms < self.config.prediction_cadence_ms
        ):
            return []

        self._set_lifecycle("PREDICT", progress=0.0, message="creating_model_b_predictions")
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
        for horizon in HORIZONS:
            model = self._bundle["models"][horizon]
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
                dataset_id="real-incremental-partitioned-v1",
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
            self._status.last_prediction_ms = timestamp
            self._status.updated_at_ms = timestamp
            self._set_lifecycle(
                "PREDICTIONS_STORED",
                progress=1.0,
                message="model_b_predictions_written_before_outcomes",
            )
        return output

    def mature_predictions(self, now_ms: int | None = None) -> None:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        self._set_lifecycle(
            "MATURE_OUTCOMES",
            progress=0.0,
            message="maturing_eligible_model_b_predictions",
        )
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
        self.ledger.mature_candles(candles, timestamp)
        self._set_lifecycle(
            "OUTCOMES_MATURED",
            progress=1.0,
            message="eligible_model_b_outcomes_resolved",
        )

    def run_cycle(self, force_train: bool = False) -> dict[str, Any]:
        self.sync_real_data()
        trained = self.train_if_due(force=force_train)
        predictions = self.predict_latest()
        self.mature_predictions()
        completed_at = int(time.time() * 1000)
        self._status.last_successful_cycle_ms = completed_at
        if self._bundle is not None:
            self._status.state = "RESEARCH_ONLY"
            self._status.reason = "model_b_research_monitoring_only"
        elif self._status.reason == "real_data_synced_directional_model_gate_pending":
            self._status.state = "WAIT"
        self._set_lifecycle(
            "LIVE_IDLE",
            progress=1.0,
            message="cycle_complete_waiting_for_next_completed_minute",
        )
        return {
            "status": asdict(self._status),
            "trained": trained,
            "predictions_created": len(predictions),
        }

    @property
    def status(self) -> PlatformStatus:
        return self._status
