"""Persistent orchestration for the parallel future-envelope model."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from .features import join_anchors_with_features
from .future_envelope import (
    HORIZONS,
    EnvelopeConfig,
    build_future_envelope_targets,
    predict_envelope,
    train_future_envelope,
)


@dataclass(frozen=True, slots=True)
class EnvelopePaths:
    targets: Path = Path("data/future_envelopes.parquet")
    model: Path = Path("models/envelope_champion.joblib")
    report: Path = Path("reports/envelope_training.json")
    predictions: Path = Path("data/envelope_predictions.parquet")


class EnvelopeEngine:
    """Train and serve observed future high/low return envelopes."""

    def __init__(self, paths: EnvelopePaths = EnvelopePaths()) -> None:
        self.paths = paths
        self.bundle: dict[str, Any] | None = None
        if paths.model.exists():
            loaded = joblib.load(paths.model)
            if isinstance(loaded, dict):
                self.bundle = loaded

    def rebuild_targets(self, prices: pd.DataFrame) -> pd.DataFrame:
        targets = build_future_envelope_targets(prices)
        self.paths.targets.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.targets.with_suffix(".parquet.tmp")
        targets.to_parquet(temporary, index=False)
        temporary.replace(self.paths.targets)
        return targets

    def train(
        self,
        targets: pd.DataFrame,
        features: pd.DataFrame,
        feature_names: list[str],
        minimum_rows: int,
        *,
        training_final_rows: int,
    ) -> bool:
        if targets.empty:
            return False
        matrix = join_anchors_with_features(targets, features)
        models: dict[int, dict[str, Any]] = {}
        reports: dict[str, Any] = {}
        all_ready = True
        for horizon in HORIZONS:
            fitted, report = train_future_envelope(
                matrix,
                feature_names,
                horizon,
                EnvelopeConfig(minimum_rows=minimum_rows),
            )
            reports[str(horizon)] = asdict(report)
            if fitted is None:
                all_ready = False
            else:
                models[horizon] = fitted

        self.paths.report.parent.mkdir(parents=True, exist_ok=True)
        self.paths.report.write_text(
            json.dumps(reports, indent=2, sort_keys=True), encoding="utf-8"
        )
        if not all_ready or len(models) != len(HORIZONS):
            return False

        bundle = {
            "model_version": f"real-envelope-{int(time.time())}",
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "models": models,
            "reports": reports,
            "source": "observed_binance_ohlc_only",
            "required_empirical_interval_coverage": 0.85,
            "training_final_rows": int(training_final_rows),
            "promoted_for_trading": False,
        }
        self.paths.model.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.model.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        temporary.replace(self.paths.model)
        self.bundle = bundle
        return True

    def predict(
        self, latest_features: pd.Series, anchor_price: float, anchor_ms: int
    ) -> list[dict[str, Any]]:
        if self.bundle is None:
            return []
        feature_names = list(self.bundle["feature_names"])
        row = pd.DataFrame([{name: latest_features.get(name) for name in feature_names}])
        issued_at_ms = int(time.time() * 1000)
        output: list[dict[str, Any]] = []
        for horizon in HORIZONS:
            estimates = predict_envelope(self.bundle["models"][horizon], row)
            max_values = sorted(
                [
                    estimates["future_max_return_q05"],
                    estimates["future_max_return_q50"],
                    estimates["future_max_return_q95"],
                ]
            )
            min_values = sorted(
                [
                    estimates["future_min_return_q05"],
                    estimates["future_min_return_q50"],
                    estimates["future_min_return_q95"],
                ]
            )
            max_low, max_mid, max_high = max_values
            min_low, min_mid, min_high = min_values
            output.append(
                {
                    "issued_at_ms": issued_at_ms,
                    "anchor_timestamp_ms": anchor_ms,
                    "anchor_price": anchor_price,
                    "horizon_minutes": horizon,
                    "model_version": self.bundle["model_version"],
                    "max_return_q05": max_low,
                    "max_return_q50": max_mid,
                    "max_return_q95": max_high,
                    "min_return_q05": min_low,
                    "min_return_q50": min_mid,
                    "min_return_q95": min_high,
                    "max_price_q50": anchor_price * (1.0 + max_mid),
                    "min_price_q50": anchor_price * (1.0 + min_mid),
                    "empirical_gate": "PASSED_85_COVERAGE",
                    "decision": "WAIT",
                    "decision_reason": "research_model_not_trading_promoted",
                }
            )
        self._append_predictions(output)
        return output

    def _append_predictions(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        new = pd.DataFrame(rows)
        if self.paths.predictions.exists():
            existing = pd.read_parquet(self.paths.predictions)
            combined = pd.concat([existing, new], ignore_index=True)
        else:
            combined = new
        combined = combined.drop_duplicates(
            ["anchor_timestamp_ms", "horizon_minutes", "model_version"], keep="first"
        ).sort_values(["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True)
        self.paths.predictions.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.predictions.with_suffix(".parquet.tmp")
        combined.to_parquet(temporary, index=False)
        temporary.replace(self.paths.predictions)

    def latest_predictions(self) -> list[dict[str, Any]]:
        if not self.paths.predictions.exists():
            return []
        frame = pd.read_parquet(self.paths.predictions)
        if frame.empty:
            return []
        latest = int(frame["anchor_timestamp_ms"].max())
        subset = frame[frame["anchor_timestamp_ms"] == latest]
        return subset.where(subset.notna(), None).to_dict(orient="records")
