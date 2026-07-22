"""XASP runtime v2: two parallel models trained only from observed data."""

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
    """Extend first-touch learning with a parallel future-envelope learner."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        super().__init__(paths, config)
        self.envelope = EnvelopeEngine()
        self._latest_envelope_predictions: list[dict[str, Any]] = []
        self._last_envelope_training_final_rows = 0
        if self.envelope.bundle is not None:
            self._last_envelope_training_final_rows = int(
                self.envelope.bundle.get("training_final_rows", 0)
            )

    def sync_real_data(self, end_ms: int | None = None) -> None:
        super().sync_real_data(end_ms)
        self.envelope.rebuild_targets(self._load_prices())

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
            return first_touch_trained

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
            self.status.state = "WAIT"
            self.status.reason = (
                "parallel_envelope_empirical_coverage_below_85_or_insufficient_rows"
            )
            self._save_status()
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
        latest = features.sort_values("timestamp_ms").iloc[-1]
        anchor_ms = int(latest["timestamp_ms"])
        anchor_price = float(latest["price"])
        self._latest_envelope_predictions = self.envelope.predict(
            latest, anchor_price, anchor_ms
        )
        self.status.updated_at_ms = timestamp
        self._save_status()
        return first_touch

    def generate_production_report(self) -> dict[str, Any]:
        ledger = self.ledger.load()
        if self.envelope.paths.predictions.exists():
            envelope_predictions = pd.read_parquet(self.envelope.paths.predictions)
        else:
            envelope_predictions = pd.DataFrame()
        prices = self._load_prices() if self.paths.prices.exists() else pd.DataFrame()
        report = build_production_report(
            ledger=ledger,
            envelope_predictions=envelope_predictions,
            prices=prices,
            runtime_status=asdict(self.status),
        )
        save_production_report(report)
        return report

    def run_cycle(self, force_train: bool = False) -> dict[str, Any]:
        self.sync_real_data()
        trained = self.train_if_due(force=force_train)
        first_touch_predictions = self.predict_latest()
        self.mature_predictions()
        report = self.generate_production_report()
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
