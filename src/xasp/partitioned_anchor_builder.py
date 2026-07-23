"""Memory-bounded monthly builder for Model B anchor outcomes."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetConfig, AnchorDatasetStore
from .dataset_state import DatasetStateStore
from .fast_anchor_dataset import _initial_dataset
from .labeling import CandlePoint
from .partitioned_horizon_store import (
    HorizonPartitionKey,
    HorizonStoreStats,
)

MINUTE_MS = 60_000
DEFAULT_PARTITION_BUILD_CHUNK_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class AnchorBuildResult:
    stats: HorizonStoreStats
    changed_partitions: tuple[HorizonPartitionKey, ...]
    rebuilt_months: tuple[str, ...]


def _normalized_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp_ms", "price", "open", "high", "low"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"anchor source prices missing columns: {sorted(missing)}")
    frame = prices[["timestamp_ms", "price", "open", "high", "low"]].copy()
    if frame.empty:
        return frame
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    frame = frame.drop_duplicates("timestamp_ms", keep="last")
    frame = frame.sort_values("timestamp_ms", ignore_index=True)
    if (frame[["price", "open", "high", "low"]] <= 0).any().any():
        raise ValueError("anchor source OHLC prices must be positive")
    if (frame["high"] < frame["low"]).any():
        raise ValueError("anchor source candle high must be greater than or equal to low")
    if (
        (frame["price"] > frame["high"])
        | (frame["price"] < frame["low"])
        | (frame["open"] > frame["high"])
        | (frame["open"] < frame["low"])
    ).any():
        raise ValueError("anchor source open/close must lie inside candle high/low")
    return frame


def _to_candles(frame: pd.DataFrame) -> list[CandlePoint]:
    return [
        CandlePoint(
            timestamp_ms=int(row.timestamp_ms),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.price),
        )
        for row in frame.itertuples(index=False)
    ]


def _month_series(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(
        frame["timestamp_ms"],
        unit="ms",
        utc=True,
    ).dt.strftime("%Y-%m")


def _partition_needs_rebuild(
    *,
    store: AnchorDatasetStore,
    month: str,
    horizons: tuple[int, ...],
    expected_anchor_rows: int,
    month_last_timestamp_ms: int,
    latest_timestamp_ms: int,
    maximum_horizon_ms: int,
) -> bool:
    if month_last_timestamp_ms >= latest_timestamp_ms - maximum_horizon_ms:
        return True
    for horizon in horizons:
        key = HorizonPartitionKey(horizon, month)
        if not store.has_partition(key):
            return True
        if store.partition_rows(key) != expected_anchor_rows:
            return True
    return False


def build_partitioned_anchor_dataset(
    prices: pd.DataFrame,
    store: AnchorDatasetStore,
    state_store: DatasetStateStore,
    config: AnchorDatasetConfig,
    *,
    chunk_rows: int = DEFAULT_PARTITION_BUILD_CHUNK_ROWS,
) -> AnchorBuildResult:
    """Build only missing or maturing UTC-month/horizon anchor partitions.

    Every monthly build includes enough real look-ahead candles for the longest
    configured horizon. Historical completed partitions are skipped on restart.
    The newest partitions are rebuilt so pending rows can mature naturally.
    """

    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    frame = _normalized_prices(prices)
    store.ensure_ready()
    if frame.empty:
        stats = store.stats()
        return AnchorBuildResult(stats, (), ())

    horizons = tuple(sorted({int(value) for value in config.horizons_minutes}))
    maximum_horizon_ms = max(horizons) * MINUTE_MS
    latest_timestamp_ms = int(frame["timestamp_ms"].max())
    months = _month_series(frame)
    changed: list[HorizonPartitionKey] = []
    rebuilt_months: list[str] = []

    for month in sorted(months.unique().tolist()):
        month_mask = months == month
        month_prices = frame.loc[month_mask]
        if month_prices.empty:
            continue
        month_start_ms = int(month_prices["timestamp_ms"].min())
        month_last_ms = int(month_prices["timestamp_ms"].max())
        expected_anchor_rows = int(len(month_prices))
        if not _partition_needs_rebuild(
            store=store,
            month=str(month),
            horizons=horizons,
            expected_anchor_rows=expected_anchor_rows,
            month_last_timestamp_ms=month_last_ms,
            latest_timestamp_ms=latest_timestamp_ms,
            maximum_horizon_ms=maximum_horizon_ms,
        ):
            continue

        source_end_ms = month_last_ms + maximum_horizon_ms
        source = frame[
            (frame["timestamp_ms"] >= month_start_ms)
            & (frame["timestamp_ms"] <= source_end_ms)
        ]
        built = _initial_dataset(
            _to_candles(source),
            config,
            chunk_rows=chunk_rows,
        )
        partition_rows = built[
            (built["anchor_timestamp_ms"] >= month_start_ms)
            & (built["anchor_timestamp_ms"] <= month_last_ms)
            & (built["horizon_minutes"].isin(horizons))
        ].copy()
        if len(partition_rows) != expected_anchor_rows * len(horizons):
            raise RuntimeError(
                "anchor partition build did not produce one row per minute/horizon: "
                f"month={month}, expected={expected_anchor_rows * len(horizons)}, "
                f"actual={len(partition_rows)}"
            )
        store.upsert(partition_rows.reindex(columns=ANCHOR_COLUMNS))
        rebuilt_months.append(str(month))
        changed.extend(HorizonPartitionKey(horizon, str(month)) for horizon in horizons)

    stats = store.stats()
    state = state_store.load()
    state.feature_watermark_ms = latest_timestamp_ms
    state.pending_label_count = stats.pending_rows
    state.finalized_label_count = stats.final_rows
    final_frame = store.load(
        start_ms=max(0, latest_timestamp_ms - maximum_horizon_ms - MINUTE_MS),
        statuses=("FINAL",),
    )
    state.finalized_label_watermark_ms = (
        None
        if final_frame.empty
        else int(final_frame["anchor_timestamp_ms"].max())
    )
    state_store.save(state)
    unique_changed = tuple(
        sorted(
            set(changed),
            key=lambda key: (key.month, key.horizon_minutes),
        )
    )
    return AnchorBuildResult(stats, unique_changed, tuple(rebuilt_months))


__all__ = [
    "AnchorBuildResult",
    "DEFAULT_PARTITION_BUILD_CHUNK_ROWS",
    "build_partitioned_anchor_dataset",
]
