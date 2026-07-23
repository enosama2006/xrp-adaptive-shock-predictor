"""Persistent Model A engine with independent horizons through eight hours."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import joblib
import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .envelope_target_store import (
    EnvelopeTargetStore,
    sync_envelope_targets_from_anchors,
)
from .fast_future_envelope import build_future_envelope_targets_fast
from .features import join_anchors_with_features
from .future_envelope import EnvelopeConfig, predict_envelope, train_future_envelope
from .horizons import RESEARCH_HORIZON_SET_VERSION, RESEARCH_HORIZONS_MINUTES

HORIZONS = RESEARCH_HORIZONS_MINUTES
TARGET_BUILD_CHUNK_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class EnvelopePaths:
    targets: Path = Path("data/future_envelopes.parquet")
    model: Path = Path("models/envelope_champion.joblib")
    report: Path = Path("reports/envelope_training.json")
    predictions: Path = Path("data/envelope_predictions.parquet")


def _valid_bundle(bundle: Any) -> bool:
    if not isinstance(bundle, dict):
        return False
    if bundle.get("horizon_set_version") != RESEARCH_HORIZON_SET_VERSION:
        return False
    models = {int(value) for value in bundle.get("models", {})}
    return bool(models) and models.issubset(set(HORIZONS))


class EnvelopeEngineV2:
    """Train, persist, and serve Model A per horizon without all-or-none coupling."""

    def __init__(self, paths: EnvelopePaths = EnvelopePaths()) -> None:
        self.paths = paths
        self.target_store = EnvelopeTargetStore(paths.targets)
        self.bundle: dict[str, Any] | None = None
        if paths.model.exists():
            loaded = joblib.load(paths.model)
            if _valid_bundle(loaded):
                self.bundle = loaded

    def rebuild_targets(self, prices: pd.DataFrame) -> pd.DataFrame:
        targets = build_future_envelope_targets_fast(
            prices,
            horizons=HORIZONS,
            chunk_rows=TARGET_BUILD_CHUNK_ROWS,
        )
        self.target_store.replace(targets)
        return targets

    def sync_targets_from_anchors(
        self,
        anchor_store: AnchorDatasetStore,
        *,
        changed_partitions: tuple[Any, ...] | None = None,
    ) -> None:
        sync_envelope_targets_from_anchors(
            anchor_store,
            self.target_store,
            changed_anchor_partitions=changed_partitions,
        )

    def train(
        self,
        targets: pd.DataFrame | None,
        features: pd.DataFrame,
        feature_names: list[str],
        minimum_rows: int,
        *,
        training_final_rows: int,
    ) -> bool:
        if targets is not None and targets.empty:
            return False
        incumbent_models: dict[int, dict[str, Any]] = {}
        if self.bundle is not None:
            incumbent_models = {
                int(horizon): model for horizon, model in self.bundle.get("models", {}).items()
            }
        models = dict(incumbent_models)
        reports: dict[str, Any] = {}
        promoted: list[int] = []
        rejected: list[int] = []

        for horizon in HORIZONS:
            target_subset = (
                self.target_store.load(
                    horizons=(horizon,),
                    statuses=("FINAL",),
                )
                if targets is None
                else targets[targets["horizon_minutes"] == horizon].copy()
            )
            matrix = join_anchors_with_features(target_subset, features)
            fitted, report = train_future_envelope(
                matrix,
                feature_names,
                horizon,
                EnvelopeConfig(
                    minimum_rows=minimum_rows,
                    embargo_ms=horizon * 60_000,
                ),
            )
            reports[str(horizon)] = asdict(report)
            if fitted is None:
                rejected.append(horizon)
            else:
                models[horizon] = fitted
                promoted.append(horizon)
            del target_subset, matrix

        self.paths.report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.paths.report.with_suffix(".json.tmp")
        temporary_report.write_text(
            json.dumps(reports, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_report.replace(self.paths.report)
        if not models:
            self.bundle = None
            return False
        if not promoted and self.bundle is not None:
            return False

        target_stats = self.target_store.stats()
        bundle: dict[str, Any] = {
            "model_version": f"real-envelope-independent-horizons-{int(time.time())}",
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "models": models,
            "reports": reports,
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "configured_horizons": list(HORIZONS),
            "available_horizons": sorted(models),
            "promoted_horizons_this_run": promoted,
            "rejected_horizons_this_run": rejected,
            "source": "observed_binance_ohlc_only",
            "required_empirical_interval_coverage": 0.85,
            "training_final_rows": int(training_final_rows),
            "target_rows": target_stats.total_rows,
            "target_partition_count": target_stats.partition_count,
            "promoted_for_trading": False,
        }
        self.paths.model.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.model.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        temporary.replace(self.paths.model)
        self.bundle = bundle
        return True

    def predict(
        self,
        latest_features: pd.Series,
        anchor_price: float,
        anchor_ms: int,
    ) -> list[dict[str, Any]]:
        if self.bundle is None:
            return []
        feature_names = list(self.bundle["feature_names"])
        row = pd.DataFrame([{name: latest_features.get(name) for name in feature_names}])
        issued_at_ms = int(time.time() * 1000)
        output: list[dict[str, Any]] = []
        models = {int(horizon): model for horizon, model in self.bundle.get("models", {}).items()}
        for horizon in sorted(models):
            estimates = predict_envelope(models[horizon], row)
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
            ["anchor_timestamp_ms", "horizon_minutes", "model_version"],
            keep="first",
        ).sort_values(
            ["anchor_timestamp_ms", "horizon_minutes"],
            ignore_index=True,
        )
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
        records = subset.where(subset.notna(), None).to_dict(orient="records")
        return cast(list[dict[str, Any]], records)


__all__ = ["EnvelopeEngineV2", "EnvelopePaths", "HORIZONS"]
