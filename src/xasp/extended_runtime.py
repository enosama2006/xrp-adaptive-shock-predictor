"""Joint eight-hour competing-risk runtime for Model B.

One completed eight-hour first-touch row is used per anchor. The event timestamp
defines the arrival hour, and every cumulative hourly probability is projected
from one fitted distribution.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from .anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore
from .competing_risk import (
    COMPETING_RISK_GATE_VERSION,
    COMPETING_RISK_MODEL_KIND,
    CompetingRiskConfig,
    predict_hourly_competing_risks,
    train_competing_risk_model,
)
from .feature_registry import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from .features import join_anchors_with_features
from .horizons import RESEARCH_HORIZON_SET_VERSION, RESEARCH_HORIZONS_MINUTES
from .partitioned_horizon_store import HorizonStoreStats
from .pipeline import IncrementalResearchPipeline, PipelineConfig, PipelinePaths
from .platform_runtime import RealDataPlatform, RuntimeConfig, RuntimePaths
from .prediction_ledger import PredictionRecord
from .target_definition import TARGET_DEFINITION_VERSION

HORIZONS = RESEARCH_HORIZONS_MINUTES
ANCHOR_REBUILD_CHUNK_ROWS = 10_000


def _bundle_model_keys(bundle: dict[str, Any]) -> set[int]:
    if bundle.get("model_kind") == COMPETING_RISK_MODEL_KIND:
        return {int(value) for value in bundle.get("available_horizons", ())}
    return {int(value) for value in bundle.get("models", {})}


def _valid_bundle(bundle: Any) -> bool:
    if not isinstance(bundle, dict):
        return False
    if bundle.get("gate_methodology_version") != COMPETING_RISK_GATE_VERSION:
        return False
    if bundle.get("horizon_set_version") != RESEARCH_HORIZON_SET_VERSION:
        return False
    if bundle.get("target_definition_version") != TARGET_DEFINITION_VERSION:
        return False
    keys = _bundle_model_keys(bundle)
    if bundle.get("model_kind") == COMPETING_RISK_MODEL_KIND:
        return (
            bundle.get("joint_model") is not None
            and keys == set(HORIZONS)
        )
    return False


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
    counts = frame.groupby("horizon_minutes")["anchor_timestamp_ms"].nunique().to_dict()
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
                self.status.last_training_final_rows = int(loaded.get("training_final_rows", 0))
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

    def _ensure_extended_anchor_horizons(self) -> HorizonStoreStats:
        store = AnchorDatasetStore(self.paths.anchors)
        stats = store.stats()
        available = {horizon for horizon, rows in stats.horizon_rows.items() if rows > 0}
        if stats.total_rows and available != set(HORIZONS):
            missing = sorted(set(HORIZONS) - available)
            raise RuntimeError(f"extended anchor partitions missing horizons: {missing}")
        return stats

    def sync_real_data(self, end_ms: int | None = None) -> None:
        super().sync_real_data(end_ms)
        anchors = self._ensure_extended_anchor_horizons()
        self.status.anchor_rows = anchors.total_rows
        self.status.final_rows = anchors.final_rows
        self.status.pending_rows = anchors.pending_rows
        self._save_status()

    def train_if_due(self, force: bool = False) -> bool:
        if not self.config.training_enabled:
            return False
        anchor_store = AnchorDatasetStore(self.paths.anchors)
        anchor_stats = anchor_store.stats()
        features = pd.read_parquet(self.paths.features)
        final_count = anchor_stats.final_rows
        due = force or (
            final_count
            >= self.status.last_training_final_rows + self.config.retrain_after_new_final_rows
        )
        if not due:
            return False

        self._set_lifecycle(
            "TRAIN_MODEL_B",
            progress=0.0,
            message="training_joint_hourly_competing_risk_model",
        )
        self._save_feature_diagnostics(features)
        feature_names = self._feature_names(features)
        anchors = anchor_store.load(
            horizons=(max(HORIZONS),),
            statuses=("FINAL",),
        )
        matrix = join_anchors_with_features(anchors, features)
        model, report = train_competing_risk_model(
            matrix,
            feature_names,
            CompetingRiskConfig(
                minimum_rows=self.config.minimum_final_rows_per_horizon,
            ),
        )
        report_payload = report.to_dict()
        reports = {
            "_joint": report_payload,
            **{
                str(horizon): {
                    "status": report.status,
                    "reason": report.reason,
                    "row_count": report.row_count,
                    "metrics": {
                        "gate_methodology_version": COMPETING_RISK_GATE_VERSION,
                        "target_definition_version": TARGET_DEFINITION_VERSION,
                        "model_kind": COMPETING_RISK_MODEL_KIND,
                        "joint_report_key": "_joint",
                    },
                }
                for horizon in HORIZONS
            },
        }

        self.paths.reports.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.paths.reports.with_suffix(".json.tmp")
        temporary_report.write_text(
            json.dumps(reports, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_report.replace(self.paths.reports)
        self.status.last_training_final_rows = final_count

        if model is None:
            if self._bundle is None:
                self.status.model_available = False
                self.status.model_version = None
                self.status.state = "WAIT"
                self.status.reason = report.reason
            self._set_lifecycle(
                "MODEL_B_WAIT",
                progress=1.0,
                message=(
                    "joint_model_challenger_rejected_champion_retained"
                    if self._bundle is not None
                    else "joint_model_not_yet_trainable"
                ),
            )
            return False

        version = f"real-joint-2pct-hourly-competing-risk-{int(time.time())}"
        bundle = {
            "model_version": version,
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "gate_methodology_version": COMPETING_RISK_GATE_VERSION,
            "model_kind": COMPETING_RISK_MODEL_KIND,
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "configured_horizons": list(HORIZONS),
            "available_horizons": list(HORIZONS),
            "joint_model": model,
            "reports": reports,
            "decision_policy": report.metrics.get("advisory_gate", {}),
            "training_final_rows": final_count,
            "source": "real_binance_public_data_only",
            "promotion_evidence": (
                "single_coherent_event_time_distribution_with_purged_temporal_"
                "validation_and_untouched_directional_policy_test"
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
        self.status.state = "RESEARCH_ONLY"
        self.status.reason = (
            "joint_first_touch_advisory_gate_passed"
            if report.metrics.get("advisory_gate", {}).get("status") == "PASS"
            else "joint_first_touch_forecast_available_advisory_wait"
        )
        self._set_lifecycle(
            "MODEL_B_RESEARCH_READY",
            progress=1.0,
            message="joint_hourly_competing_risk_model_ready",
        )
        return True

    def predict_latest(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if self._bundle is None:
            self.status.state = "WAIT"
            self.status.reason = "no_joint_competing_risk_forecast_available"
            self._save_status()
            return []
        if (
            self.status.last_prediction_ms is not None
            and timestamp - self.status.last_prediction_ms < self.config.prediction_cadence_ms
        ):
            return []

        self._set_lifecycle(
            "PREDICT",
            progress=0.0,
            message="creating_coherent_joint_first_touch_timeline",
        )
        features = pd.read_parquet(self.paths.features)
        if features.empty:
            return []
        latest = features.sort_values("timestamp_ms").iloc[-1]
        anchor_ms = int(latest["timestamp_ms"])
        anchor_price = float(latest["price"])
        feature_names = list(self._bundle["feature_names"])
        row = pd.DataFrame([{name: latest.get(name) for name in feature_names}])
        joint_model = self._bundle.get("joint_model")
        if joint_model is None:
            self.status.state = "WAIT"
            self.status.reason = "joint_competing_risk_model_missing"
            self._save_status()
            return []
        timeline = predict_hourly_competing_risks(joint_model, row)
        selected = timeline[-1]
        event_probability = float(selected["event_probability"])
        up = float(selected["p_up_02"])
        down = float(selected["p_down_02"])
        directional_confidence = (
            max(up, down) / event_probability if event_probability else 0.5
        )
        bias = "LONG" if up > down else "SHORT" if down > up else "WAIT"
        advisory = self._bundle.get("decision_policy", {})
        policy = advisory.get("selected_policy")
        decision = "WAIT"
        decision_reason = "joint_forecast_available_advisory_gate_wait"
        if advisory.get("status") == "PASS" and isinstance(policy, dict):
            if (
                event_probability >= float(policy["minimum_event_probability"])
                and directional_confidence
                >= float(policy["minimum_direction_confidence"])
                and bias in {"LONG", "SHORT"}
            ):
                decision = bias
                decision_reason = "validated_joint_competing_risk_signal"
            else:
                decision_reason = "validated_policy_thresholds_not_met_now"

        records: list[PredictionRecord] = []
        output: list[dict[str, Any]] = []
        for projection in timeline:
            horizon = int(projection["horizon_minutes"])
            record = PredictionRecord(
                created_at_ms=timestamp,
                anchor_timestamp_ms=anchor_ms,
                anchor_price=anchor_price,
                horizon_minutes=horizon,
                model_version=str(self._bundle["model_version"]),
                dataset_id=(
                    f"real-partitioned-{RESEARCH_HORIZON_SET_VERSION}-"
                    f"{TARGET_DEFINITION_VERSION}"
                ),
                feature_schema_version=str(
                    self._bundle.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
                ),
                p_up_02=float(projection["p_up_02"]),
                p_down_02=float(projection["p_down_02"]),
                p_no_event=float(projection["p_no_event"]),
                decision=decision,
                decision_reason=decision_reason,
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
                message="coherent_joint_first_touch_timeline_stored",
            )
        return output


__all__ = [
    "ExtendedHorizonRealDataPlatform",
    "HORIZONS",
]
