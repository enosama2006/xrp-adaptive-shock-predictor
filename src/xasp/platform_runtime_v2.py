"""XASP runtime v2: two independent models trained only from observed data."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .envelope_engine import EnvelopeEngine
from .platform_runtime import PlatformStatus, RealDataPlatform, RuntimeConfig, RuntimePaths
from .production_report import build_production_report, save_production_report


class RealDataPlatformV2(RealDataPlatform):
    """Extend first-touch learning with an independent future-excursion learner."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        super().__init__(paths, config)
        self.envelope = EnvelopeEngine()
        self._latest_envelope_predictions: list[dict[str, Any]] = []
        self._last_envelope_training_final_rows = 0
        if self.envelope.bundle is not None:
            self._last_envelope_training_final_rows = int(
                self.envelope.bundle.get("training_final_rows", 0)
            )
        self._refresh_research_state(save=False)

    def _refresh_research_state(self, *, save: bool = True) -> None:
        touch_ready = self._bundle is not None
        envelope_ready = self.envelope.bundle is not None
        if touch_ready and envelope_ready:
            self.status.state = "RESEARCH_ONLY"
            self.status.reason = "dual_models_research_monitoring_only"
        elif envelope_ready:
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "model_a_ready_model_b_directional_gate_wait"
        elif touch_ready:
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "model_b_ready_model_a_evidence_gate_wait"
        else:
            self.status.state = "WAIT"
            if self.status.reason in {
                "not_started",
                "real_data_synced_model_gate_pending",
                "real_data_synced_directional_model_gate_pending",
            }:
                self.status.reason = "both_model_evidence_gates_pending"
        if save:
            self._save_status()

    def sync_real_data(self, end_ms: int | None = None) -> None:
        super().sync_real_data(end_ms)
        self._set_lifecycle(
            "BUILD_TARGETS_A",
            progress=0.0,
            message="building_observed_future_excursion_targets",
        )
        self.envelope.rebuild_targets(self._load_prices())
        self._refresh_research_state(save=False)
        self._set_lifecycle(
            "TARGETS_A_READY",
            progress=1.0,
            message="model_a_targets_ready_for_training_gate",
        )

    def train_if_due(self, force: bool = False) -> bool:
        first_touch_trained = super().train_if_due(force=force)
        anchors = AnchorDatasetStore(self.paths.anchors).load()
        final_count = int((anchors["status"] == "FINAL").sum()) if not anchors.empty else 0
        enough_rows = final_count >= self.config.minimum_final_rows_per_horizon * 4
        enough_new_rows = (
            final_count
            >= self._last_envelope_training_final_rows
            + self.config.retrain_after_new_final_rows
        )
        if not (force or (enough_rows and enough_new_rows)):
            self._refresh_research_state()
            return first_touch_trained

        had_envelope_champion = self.envelope.bundle is not None
        self._set_lifecycle(
            "TRAIN_MODEL_A",
            progress=0.0,
            message="training_future_excursion_challenger",
        )
        features = pd.read_parquet(self.paths.features)
        targets = self.envelope.rebuild_targets(self._load_prices())
        feature_names = self._feature_names(features)
        envelope_trained = self.envelope.train(
            targets,
            features,
            feature_names,
            self.config.minimum_final_rows_per_horizon,
            training_final_rows=final_count,
        )
        self._last_envelope_training_final_rows = final_count
        if not envelope_trained:
            self._refresh_research_state(save=False)
            self._set_lifecycle(
                "MODEL_A_WAIT",
                progress=1.0,
                message=(
                    "model_a_challenger_rejected_champion_retained"
                    if had_envelope_champion
                    else "model_a_evidence_gate_failed_or_insufficient"
                ),
            )
        else:
            self._refresh_research_state(save=False)
            self._set_lifecycle(
                "MODEL_A_RESEARCH_READY",
                progress=1.0,
                message="model_a_empirical_gate_passed",
            )
        return first_touch_trained or envelope_trained

    def predict_latest(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        first_touch = super().predict_latest(now_ms)
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if self.envelope.bundle is None:
            self._latest_envelope_predictions = []
            return first_touch
        features = pd.read_parquet(self.paths.features)
        if features.empty:
            self._latest_envelope_predictions = []
            return first_touch

        self._set_lifecycle(
            "PREDICT_MODEL_A",
            progress=0.0,
            message="creating_future_excursion_predictions",
        )
        latest = features.sort_values("timestamp_ms").iloc[-1]
        anchor_ms = int(latest["timestamp_ms"])
        anchor_price = float(latest["price"])
        self._latest_envelope_predictions = self.envelope.predict(
            latest,
            anchor_price,
            anchor_ms,
        )
        self.status.updated_at_ms = timestamp
        self._set_lifecycle(
            "MODEL_A_PREDICTIONS_STORED",
            progress=1.0,
            message="model_a_predictions_written_before_outcomes",
        )
        return first_touch

    def _active_first_touch_ledger(self) -> pd.DataFrame:
        if self._bundle is None:
            return pd.DataFrame()
        ledger = self.ledger.load()
        if ledger.empty:
            return ledger
        version = str(self._bundle["model_version"])
        return ledger[ledger["model_version"] == version].copy()

    def _active_envelope_predictions(self) -> pd.DataFrame:
        if self.envelope.bundle is None or not self.envelope.paths.predictions.exists():
            return pd.DataFrame()
        predictions = pd.read_parquet(self.envelope.paths.predictions)
        if predictions.empty:
            return predictions
        version = str(self.envelope.bundle["model_version"])
        return predictions[predictions["model_version"] == version].copy()

    def generate_production_report(self) -> dict[str, Any]:
        self._set_lifecycle(
            "REPORT",
            progress=0.0,
            message="building_dual_model_production_report",
        )
        ledger = self._active_first_touch_ledger()
        envelope_predictions = self._active_envelope_predictions()
        prices = self._load_prices() if self.paths.prices.exists() else pd.DataFrame()
        report = build_production_report(
            ledger=ledger,
            envelope_predictions=envelope_predictions,
            prices=prices,
            runtime_status=asdict(self.status),
        )
        save_production_report(report)
        self._set_lifecycle(
            "REPORT_READY",
            progress=1.0,
            message="production_report_saved",
        )
        return report

    def run_cycle(self, force_train: bool = False) -> dict[str, Any]:
        self.sync_real_data()
        trained = self.train_if_due(force=force_train)
        first_touch_predictions = self.predict_latest()
        self.mature_predictions()
        self._refresh_research_state()
        report = self.generate_production_report()
        completed_at = int(time.time() * 1000)
        self.status.last_successful_cycle_ms = completed_at
        self._refresh_research_state(save=False)
        self._set_lifecycle(
            "LIVE_IDLE",
            progress=1.0,
            message="cycle_complete_waiting_for_next_completed_minute",
        )
        return {
            "status": asdict(self.status),
            "trained": trained,
            "first_touch_predictions_created": len(first_touch_predictions),
            "envelope_predictions_created": len(self._latest_envelope_predictions),
            "envelope_model_available": self.envelope.bundle is not None,
            "production_report": report,
        }


__all__ = [
    "PlatformStatus",
    "RealDataPlatformV2",
    "RuntimeConfig",
    "RuntimePaths",
]
