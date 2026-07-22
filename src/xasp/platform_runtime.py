"""Real-data runtime for incremental collection, training, and governed predictions.

No synthetic rows or heuristic probabilities are permitted. When real data or a
validated fitted model is unavailable, the runtime emits WAIT with an explicit reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any

import joblib
import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .baseline import BaselineConfig, train_multinomial_baseline
from .feature_registry import (
    SCHEMA_VERSION as FEATURE_SCHEMA_VERSION,
    audit_feature_columns,
    select_model_feature_names,
)
from .features import build_feature_diagnostics, build_price_features, join_anchors_with_features
from .pipeline import IncrementalResearchPipeline, PipelineConfig, PipelinePaths
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


class RealDataPlatform:
    """One-process research runtime backed exclusively by public exchange data."""

    def __init__(self, paths: RuntimePaths, config: RuntimeConfig) -> None:
        self.paths = paths
        self.config = config
        self.pipeline = IncrementalResearchPipeline(
            PipelinePaths(paths.prices, paths.anchors, paths.state),
            PipelineConfig(symbol=config.symbol, bootstrap_start_ms=config.bootstrap_start_ms),
        )
        self.ledger = PredictionLedger(paths.ledger)
        self._bundle: dict[str, Any] | None = None
        self._status = self._load_status()
        if paths.models.exists():
            loaded = joblib.load(paths.models)
            if isinstance(loaded, dict):
                self._bundle = loaded

    def _load_status(self) -> PlatformStatus:
        if self.paths.status.exists():
            payload = json.loads(self.paths.status.read_text(encoding="utf-8"))
            return PlatformStatus(**payload)
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

    def _save_status(self) -> None:
        self.paths.status.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.status.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(self._status), indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.paths.status)

    def _load_prices(self) -> pd.DataFrame:
        frame = pd.read_parquet(self.paths.prices)
        required = {"timestamp_ms", "price"}
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
        """Select only explicitly registered features; unknown columns fail closed."""

        return select_model_feature_names(features)

    def sync_real_data(self, end_ms: int | None = None) -> None:
        cutoff = int(time.time() * 1000) if end_ms is None else end_ms
        self.pipeline.run(cutoff)
        prices = self._load_prices()
        features = build_price_features(prices)
        self.paths.features.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.features.with_suffix(".parquet.tmp")
        features.to_parquet(temporary, index=False)
        temporary.replace(self.paths.features)

        anchors = AnchorDatasetStore(self.paths.anchors).load()
        final_rows = int((anchors["status"] == "FINAL").sum()) if not anchors.empty else 0
        pending_rows = int((anchors["status"] == "PENDING").sum()) if not anchors.empty else 0
        self._status.price_rows = len(prices)
        self._status.anchor_rows = len(anchors)
        self._status.final_rows = final_rows
        self._status.pending_rows = pending_rows
        self._status.data_start_ms = None if prices.empty else int(prices["timestamp_ms"].min())
        self._status.data_end_ms = None if prices.empty else int(prices["timestamp_ms"].max())
        self._status.updated_at_ms = cutoff
        self._status.state = "WAIT"
        self._status.reason = "real_data_synced_model_gate_pending"
        self._save_status()

    def _save_feature_diagnostics(self, features: pd.DataFrame) -> None:
        report = build_feature_diagnostics(features)
        report["generated_at_ms"] = int(time.time() * 1000)
        report["feature_schema_version"] = FEATURE_SCHEMA_VERSION
        report["selection_audit"] = audit_feature_columns(features).to_dict()
        self.paths.feature_diagnostics.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.feature_diagnostics.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.paths.feature_diagnostics)

    def train_if_due(self, force: bool = False) -> bool:
        if not self.config.training_enabled:
            return False
        anchors = AnchorDatasetStore(self.paths.anchors).load()
        features = pd.read_parquet(self.paths.features)
        final_count = int((anchors["status"] == "FINAL").sum())
        due = force or (
            final_count
            >= self._status.last_training_final_rows
            + self.config.retrain_after_new_final_rows
        )
        if not due:
            return False

        self._save_feature_diagnostics(features)
        matrix = join_anchors_with_features(anchors, features)
        feature_names = self._feature_names(features)
        models: dict[int, Any] = {}
        reports: dict[str, Any] = {}
        all_ready = True
        for horizon in HORIZONS:
            subset = matrix[matrix["horizon_minutes"] == horizon].copy()
            model, report = train_multinomial_baseline(
                subset,
                feature_names,
                BaselineConfig(minimum_rows=self.config.minimum_final_rows_per_horizon),
            )
            reports[str(horizon)] = asdict(report)
            if model is None:
                all_ready = False
            else:
                models[horizon] = model

        self.paths.reports.parent.mkdir(parents=True, exist_ok=True)
        self.paths.reports.write_text(
            json.dumps(reports, indent=2, sort_keys=True), encoding="utf-8"
        )
        if not all_ready or len(models) != len(HORIZONS):
            self._status.state = "WAIT"
            self._status.reason = "insufficient_real_training_evidence"
            self._status.last_training_final_rows = final_count
            self._save_status()
            return False

        version = f"real-logistic-{int(time.time())}"
        bundle = {
            "model_version": version,
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "models": models,
            "reports": reports,
            "training_final_rows": final_count,
            "source": "real_binance_public_data_only",
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
        self._status.reason = "real_model_fitted_not_yet_trading_promoted"
        self._save_status()
        return True

    def predict_latest(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if self._bundle is None:
            self._status.state = "WAIT"
            self._status.reason = "no_fitted_real_model"
            self._save_status()
            return []
        if (
            self._status.last_prediction_ms is not None
            and timestamp - self._status.last_prediction_ms
            < self.config.prediction_cadence_ms
        ):
            return []

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
                dataset_id="real-incremental",
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
            self._save_status()
        return output

    def mature_predictions(self, now_ms: int | None = None) -> None:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        prices = self._load_prices()
        from .labeling import PricePoint

        points = [
            PricePoint(int(row.timestamp_ms), float(row.price))
            for row in prices.itertuples(index=False)
        ]
        self.ledger.mature(points, timestamp)

    def run_cycle(self, force_train: bool = False) -> dict[str, Any]:
        self.sync_real_data()
        trained = self.train_if_due(force=force_train)
        predictions = self.predict_latest()
        self.mature_predictions()
        return {
            "status": asdict(self._status),
            "trained": trained,
            "predictions_created": len(predictions),
        }

    @property
    def status(self) -> PlatformStatus:
        return self._status
