"""XASP runtime v2: two independent models trained only from observed data."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .envelope_engine_v2 import HORIZONS, EnvelopeEngineV2, EnvelopePaths
from .memory_safe_runtime import MemorySafeExtendedHorizonPlatform
from .platform_runtime import PlatformStatus, RuntimeConfig, RuntimePaths
from .production_report_v2 import build_production_report, save_production_report


class RealDataPlatformV2(MemorySafeExtendedHorizonPlatform):
    """Run independent Model A and Model B horizons through eight hours."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        super().__init__(paths, config)
        self.envelope = EnvelopeEngineV2(
            EnvelopePaths(
                targets=paths.prices.parent / "future_envelopes.parquet",
                model=paths.models.parent / "envelope_champion.joblib",
                report=paths.reports.parent / "envelope_training.json",
                predictions=paths.ledger.parent / "envelope_predictions.parquet",
            )
        )
        self._latest_envelope_predictions: list[dict[str, Any]] = []
        self._last_envelope_training_final_rows = 0
        if self.envelope.bundle is not None:
            self._last_envelope_training_final_rows = int(
                self.envelope.bundle.get("training_final_rows", 0)
            )
        self._refresh_research_state(save=False)

    @staticmethod
    def _available_horizons(bundle: dict[str, Any] | None) -> set[int]:
        if bundle is None:
            return set()
        return {int(value) for value in bundle.get("models", {})}

    def _refresh_research_state(self, *, save: bool = True) -> None:
        configured = set(HORIZONS)
        touch_horizons = self._available_horizons(self._bundle)
        envelope_horizons = self._available_horizons(self.envelope.bundle)
        touch_any = bool(touch_horizons)
        envelope_any = bool(envelope_horizons)
        touch_all = touch_horizons == configured
        envelope_all = envelope_horizons == configured

        if touch_all and envelope_all:
            self.status.state = "RESEARCH_ONLY"
            self.status.reason = "dual_models_all_horizons_research_monitoring_only"
        elif touch_any and envelope_any:
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "dual_models_some_independent_horizons_ready_others_wait"
        elif envelope_any:
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "model_a_some_horizons_ready_model_b_wait"
        elif touch_any:
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "model_b_some_horizons_ready_model_a_wait"
        else:
            self.status.state = "WAIT"
            if self.status.reason in {
                "not_started",
                "real_data_synced_model_gate_pending",
                "real_data_synced_directional_model_gate_pending",
            }:
                self.status.reason = "both_model_independent_horizon_gates_pending"
        if save:
            self._save_status()

    def sync_real_data(self, end_ms: int | None = None) -> None:
        """Sync candles and anchors; defer the large Model A target rebuild until training."""

        super().sync_real_data(end_ms)
        self._refresh_research_state()

    def train_if_due(self, force: bool = False) -> bool:
        first_touch_trained = super().train_if_due(force=force)
        anchors = AnchorDatasetStore(self.paths.anchors).load()
        final_count = int((anchors["status"] == "FINAL").sum()) if not anchors.empty else 0
        per_horizon_counts = (
            anchors[anchors["status"] == "FINAL"]
            .groupby("horizon_minutes")
            .size()
            .to_dict()
            if not anchors.empty
            else {}
        )
        enough_rows = any(
            int(per_horizon_counts.get(horizon, 0))
            >= self.config.minimum_final_rows_per_horizon
            for horizon in HORIZONS
        )
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
            "BUILD_TARGETS_A",
            progress=0.0,
            message="building_observed_future_excursion_targets_through_8h",
        )
        features = pd.read_parquet(self.paths.features)
        targets = self.envelope.rebuild_targets(self._load_prices())
        self._set_lifecycle(
            "TARGETS_A_READY",
            progress=1.0,
            message="model_a_extended_horizon_targets_ready",
        )
        self._set_lifecycle(
            "TRAIN_MODEL_A",
            progress=0.0,
            message="training_future_excursion_independent_horizon_challengers",
        )
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
                    "model_a_challengers_rejected_existing_horizons_retained"
                    if had_envelope_champion
                    else "model_a_all_horizons_wait"
                ),
            )
        else:
            self._refresh_research_state(save=False)
            self._set_lifecycle(
                "MODEL_A_RESEARCH_READY",
                progress=1.0,
                message="model_a_independent_horizon_gates_evaluated",
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
            message="creating_future_excursion_predictions_for_available_horizons",
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
            message="model_a_available_horizon_predictions_stored",
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
            message="building_extended_horizon_production_report",
        )
        ledger = self._active_first_touch_ledger()
        envelope_predictions = self._active_envelope_predictions()
        prices = self._load_prices() if self.price_store.exists else pd.DataFrame()
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
            message="extended_horizon_production_report_saved",
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
            "first_touch_available_horizons": sorted(
                self._available_horizons(self._bundle)
            ),
            "envelope_available_horizons": sorted(
                self._available_horizons(self.envelope.bundle)
            ),
            "production_report": report,
        }


__all__ = [
    "PlatformStatus",
    "RealDataPlatformV2",
    "RuntimeConfig",
    "RuntimePaths",
]
