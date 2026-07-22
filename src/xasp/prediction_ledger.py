"""Immutable prediction ledger with delayed outcome maturation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .labeling import BarrierConfig, PricePoint, label_first_touch

LEDGER_COLUMNS = [
    "prediction_id",
    "created_at_ms",
    "anchor_timestamp_ms",
    "anchor_price",
    "horizon_minutes",
    "horizon_end_ms",
    "model_version",
    "dataset_id",
    "feature_schema_version",
    "p_up_10",
    "p_down_10",
    "p_no_event",
    "decision",
    "decision_reason",
    "status",
    "actual_label",
    "touch_timestamp_ms",
    "touch_price",
    "resolved_at_ms",
    "record_hash",
]


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    created_at_ms: int
    anchor_timestamp_ms: int
    anchor_price: float
    horizon_minutes: int
    model_version: str
    dataset_id: str
    feature_schema_version: str
    p_up_10: float
    p_down_10: float
    p_no_event: float
    decision: str = "WAIT"
    decision_reason: str = "research_only"

    def __post_init__(self) -> None:
        if self.created_at_ms < 0 or self.anchor_timestamp_ms < 0:
            raise ValueError("timestamps must be non-negative")
        if self.anchor_price <= 0 or self.horizon_minutes <= 0:
            raise ValueError("anchor_price and horizon_minutes must be positive")
        probabilities = (self.p_up_10, self.p_down_10, self.p_no_event)
        if any(value < 0 or value > 1 for value in probabilities):
            raise ValueError("probabilities must be in [0, 1]")
        if abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError("probabilities must sum to one")
        if self.decision not in {"WAIT", "LONG", "SHORT"}:
            raise ValueError("unsupported decision")

    @property
    def horizon_end_ms(self) -> int:
        return self.anchor_timestamp_ms + self.horizon_minutes * 60_000

    @property
    def prediction_id(self) -> str:
        identity = "|".join(
            [
                str(self.anchor_timestamp_ms),
                str(self.horizon_minutes),
                self.model_version,
                self.dataset_id,
                self.feature_schema_version,
            ]
        )
        return sha256(identity.encode("utf-8")).hexdigest()[:24]

    def to_row(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            {
                "prediction_id": self.prediction_id,
                "horizon_end_ms": self.horizon_end_ms,
                "status": "PENDING",
                "actual_label": None,
                "touch_timestamp_ms": None,
                "touch_price": None,
                "resolved_at_ms": None,
            }
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload["record_hash"] = sha256(canonical.encode("utf-8")).hexdigest()
        return {column: payload[column] for column in LEDGER_COLUMNS}


class PredictionLedger:
    """Append-only prediction store; only outcome fields may mature later."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=LEDGER_COLUMNS)
        frame = pd.read_parquet(self.path)
        missing = set(LEDGER_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"prediction ledger missing columns: {sorted(missing)}")
        return frame[LEDGER_COLUMNS].sort_values(
            ["anchor_timestamp_ms", "horizon_minutes", "prediction_id"], ignore_index=True
        )

    def _save(self, frame: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized = frame[LEDGER_COLUMNS].sort_values(
            ["anchor_timestamp_ms", "horizon_minutes", "prediction_id"], ignore_index=True
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        normalized.to_parquet(temporary, index=False)
        temporary.replace(self.path)

    def append(self, records: Iterable[PredictionRecord]) -> pd.DataFrame:
        existing = self.load()
        new_rows = pd.DataFrame([record.to_row() for record in records], columns=LEDGER_COLUMNS)
        combined = pd.concat([existing, new_rows], ignore_index=True)
        duplicates = combined.duplicated("prediction_id", keep=False)
        if duplicates.any():
            duplicate_rows = combined[duplicates]
            for _, group in duplicate_rows.groupby("prediction_id"):
                if group["record_hash"].nunique() != 1:
                    raise ValueError("prediction_id collision with different immutable payload")
            combined = combined.drop_duplicates("prediction_id", keep="first")
        self._save(combined)
        return self.load()

    def mature(self, prices: Iterable[PricePoint], resolved_at_ms: int) -> pd.DataFrame:
        points = sorted(prices, key=lambda point: point.timestamp_ms)
        frame = self.load()
        if frame.empty:
            return frame
        eligible = (frame["status"] == "PENDING") & (frame["horizon_end_ms"] <= resolved_at_ms)
        for index in frame.index[eligible]:
            anchor = PricePoint(
                timestamp_ms=int(frame.at[index, "anchor_timestamp_ms"]),
                price=float(frame.at[index, "anchor_price"]),
            )
            horizon_ms = int(frame.at[index, "horizon_minutes"]) * 60_000
            result = label_first_touch(
                anchor,
                points,
                BarrierConfig(horizon_ms=horizon_ms),
            )
            frame.at[index, "actual_label"] = result.label.value
            frame.at[index, "touch_timestamp_ms"] = result.touch_timestamp_ms
            frame.at[index, "touch_price"] = result.touch_price
            frame.at[index, "resolved_at_ms"] = resolved_at_ms
            frame.at[index, "status"] = (
                "FINAL" if result.label.value not in {"INCOMPLETE", "AMBIGUOUS"} else "EXCLUDED"
            )
        self._save(frame)
        return self.load()
