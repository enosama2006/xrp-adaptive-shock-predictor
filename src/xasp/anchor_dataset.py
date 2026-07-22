"""Incremental anchor dataset builder with restart-safe pending/final labels."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .dataset_state import DatasetState, DatasetStateStore
from .labeling import BarrierConfig, BarrierLabel, PricePoint, label_first_touch

ANCHOR_COLUMNS = [
    "anchor_timestamp_ms",
    "anchor_price",
    "horizon_minutes",
    "horizon_end_ms",
    "upper_barrier_price",
    "lower_barrier_price",
    "max_price",
    "min_price",
    "max_return",
    "min_return",
    "label",
    "touch_timestamp_ms",
    "touch_price",
    "status",
    "reason",
]


@dataclass(frozen=True, slots=True)
class AnchorDatasetConfig:
    horizons_minutes: tuple[int, ...] = (15, 30, 45, 60)
    upper_return: float = 0.10
    lower_return: float = -0.10
    cadence_ms: int = 60_000

    def __post_init__(self) -> None:
        if not self.horizons_minutes or any(value <= 0 for value in self.horizons_minutes):
            raise ValueError("horizons_minutes must contain positive values")
        if self.upper_return <= 0 or self.lower_return >= 0:
            raise ValueError("barriers must straddle zero")
        if self.cadence_ms <= 0:
            raise ValueError("cadence_ms must be positive")


class AnchorDatasetStore:
    """Append/replace a compact Parquet anchor table atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=ANCHOR_COLUMNS)
        frame = pd.read_parquet(self.path)
        missing = set(ANCHOR_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"anchor dataset missing columns: {sorted(missing)}")
        return frame[ANCHOR_COLUMNS].sort_values(
            ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True
        )

    def save(self, frame: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized = frame[ANCHOR_COLUMNS].sort_values(
            ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        normalized.to_parquet(temporary, index=False)
        temporary.replace(self.path)


def _normalize_prices(prices: Iterable[PricePoint]) -> list[PricePoint]:
    ordered = sorted(prices, key=lambda point: point.timestamp_ms)
    deduplicated: list[PricePoint] = []
    for point in ordered:
        if deduplicated and point.timestamp_ms == deduplicated[-1].timestamp_ms:
            deduplicated[-1] = point
        else:
            deduplicated.append(point)
    return deduplicated


def _candidate_anchor_timestamps(
    points: list[PricePoint],
    last_anchor_timestamp_ms: int | None,
    cadence_ms: int,
) -> list[int]:
    if not points:
        return []
    start = points[0].timestamp_ms
    if last_anchor_timestamp_ms is not None:
        start = max(start, last_anchor_timestamp_ms + cadence_ms)
    first = ((start + cadence_ms - 1) // cadence_ms) * cadence_ms
    return list(range(first, points[-1].timestamp_ms + 1, cadence_ms))


def _price_at_or_before(points: list[PricePoint], timestamp_ms: int) -> PricePoint | None:
    candidate: PricePoint | None = None
    for point in points:
        if point.timestamp_ms > timestamp_ms:
            break
        candidate = point
    return candidate


def _build_row(
    anchor: PricePoint,
    future: list[PricePoint],
    horizon_minutes: int,
    upper_return: float,
    lower_return: float,
    latest_timestamp_ms: int,
) -> dict[str, object]:
    horizon_ms = horizon_minutes * 60_000
    horizon_end = anchor.timestamp_ms + horizon_ms
    path = [point for point in future if anchor.timestamp_ms < point.timestamp_ms <= horizon_end]
    max_price = max((point.price for point in path), default=None)
    min_price = min((point.price for point in path), default=None)

    if latest_timestamp_ms < horizon_end:
        label = BarrierLabel.INCOMPLETE
        touch_timestamp_ms = None
        touch_price = None
        status = "PENDING"
        reason = "horizon_not_mature"
    else:
        result = label_first_touch(
            anchor,
            path,
            BarrierConfig(
                upper_return=upper_return,
                lower_return=lower_return,
                horizon_ms=horizon_ms,
            ),
        )
        label = result.label
        touch_timestamp_ms = result.touch_timestamp_ms
        touch_price = result.touch_price
        status = "FINAL" if result.label is not BarrierLabel.INCOMPLETE else "EXCLUDED"
        reason = result.reason

    return {
        "anchor_timestamp_ms": anchor.timestamp_ms,
        "anchor_price": anchor.price,
        "horizon_minutes": horizon_minutes,
        "horizon_end_ms": horizon_end,
        "upper_barrier_price": anchor.price * (1 + upper_return),
        "lower_barrier_price": anchor.price * (1 + lower_return),
        "max_price": max_price,
        "min_price": min_price,
        "max_return": None if max_price is None else (max_price / anchor.price) - 1,
        "min_return": None if min_price is None else (min_price / anchor.price) - 1,
        "label": label.value,
        "touch_timestamp_ms": touch_timestamp_ms,
        "touch_price": touch_price,
        "status": status,
        "reason": reason,
    }


def update_anchor_dataset(
    prices: Iterable[PricePoint],
    store: AnchorDatasetStore,
    state_store: DatasetStateStore,
    config: AnchorDatasetConfig = AnchorDatasetConfig(),
) -> pd.DataFrame:
    """Append new anchors and finalize matured pending rows without full rebuild."""

    points = _normalize_prices(prices)
    existing = store.load()
    state = state_store.load()
    if not points:
        return existing

    latest_timestamp_ms = points[-1].timestamp_ms
    if existing.empty:
        last_anchor_timestamp_ms = None
    else:
        last_anchor_timestamp_ms = int(existing["anchor_timestamp_ms"].max())

    new_rows: list[dict[str, object]] = []
    for timestamp_ms in _candidate_anchor_timestamps(
        points, last_anchor_timestamp_ms, config.cadence_ms
    ):
        anchor = _price_at_or_before(points, timestamp_ms)
        if anchor is None:
            continue
        normalized_anchor = PricePoint(timestamp_ms=timestamp_ms, price=anchor.price)
        for horizon in config.horizons_minutes:
            new_rows.append(
                _build_row(
                    normalized_anchor,
                    points,
                    horizon,
                    config.upper_return,
                    config.lower_return,
                    latest_timestamp_ms,
                )
            )

    combined = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
    if not combined.empty:
        pending_mask = combined["status"] == "PENDING"
        matured_mask = pending_mask & (combined["horizon_end_ms"] <= latest_timestamp_ms)
        for index in combined.index[matured_mask]:
            anchor = PricePoint(
                timestamp_ms=int(combined.at[index, "anchor_timestamp_ms"]),
                price=float(combined.at[index, "anchor_price"]),
            )
            replacement = _build_row(
                anchor,
                points,
                int(combined.at[index, "horizon_minutes"]),
                config.upper_return,
                config.lower_return,
                latest_timestamp_ms,
            )
            for key, value in replacement.items():
                combined.at[index, key] = value

        combined = combined.drop_duplicates(
            ["anchor_timestamp_ms", "horizon_minutes"], keep="last"
        )

    store.save(combined)
    state.feature_watermark_ms = latest_timestamp_ms
    state.pending_label_count = int((combined["status"] == "PENDING").sum())
    state.finalized_label_count = int((combined["status"] == "FINAL").sum())
    final_rows = combined[combined["status"] == "FINAL"]
    state.finalized_label_watermark_ms = (
        None if final_rows.empty else int(final_rows["anchor_timestamp_ms"].max())
    )
    state_store.save(state)
    return store.load()
