"""Vectorized OHLC anchor builder for large historical bootstraps.

The legacy candle builder repeatedly scanned the complete candle list for every
anchor and every horizon. On a one-year minute dataset that is effectively
quadratic and can keep the API unresponsive for hours. This module builds the
initial dataset with bounded NumPy window chunks, then updates only new and
maturing live-tail rows.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd

from .anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetConfig, AnchorDatasetStore
from .dataset_state import DatasetStateStore
from .labeling import (
    BarrierConfig,
    BarrierLabel,
    CandlePoint,
    PricePoint,
    label_first_touch_candles,
)

MINUTE_MS = 60_000
DEFAULT_CHUNK_ROWS = 100_000


def _normalize_candles(candles: Iterable[CandlePoint]) -> list[CandlePoint]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp_ms)
    deduplicated: list[CandlePoint] = []
    for candle in ordered:
        if deduplicated and candle.timestamp_ms == deduplicated[-1].timestamp_ms:
            deduplicated[-1] = candle
        else:
            deduplicated.append(candle)
    return deduplicated


def _save_and_advance_state(
    frame: pd.DataFrame,
    *,
    store: AnchorDatasetStore,
    state_store: DatasetStateStore,
    latest_timestamp_ms: int,
) -> pd.DataFrame:
    normalized = frame.drop_duplicates(
        ["anchor_timestamp_ms", "horizon_minutes"], keep="last"
    )
    store.save(normalized)
    state = state_store.load()
    state.feature_watermark_ms = latest_timestamp_ms
    state.pending_label_count = int((normalized["status"] == "PENDING").sum())
    state.finalized_label_count = int((normalized["status"] == "FINAL").sum())
    final_rows = normalized[normalized["status"] == "FINAL"]
    state.finalized_label_watermark_ms = (
        None if final_rows.empty else int(final_rows["anchor_timestamp_ms"].max())
    )
    state_store.save(state)
    return store.load()


def _partial_extrema(
    row_indices: np.ndarray,
    timestamps: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    horizon_end: np.ndarray,
    max_price: np.ndarray,
    min_price: np.ndarray,
) -> None:
    for row_index in row_indices.tolist():
        end = int(np.searchsorted(timestamps, horizon_end[row_index], side="right"))
        if end <= row_index + 1:
            continue
        max_price[row_index] = float(np.max(highs[row_index + 1 : end]))
        min_price[row_index] = float(np.min(lows[row_index + 1 : end]))


def _initial_dataset(
    points: list[CandlePoint],
    config: AnchorDatasetConfig,
    *,
    chunk_rows: int,
) -> pd.DataFrame:
    timestamps = np.fromiter((point.timestamp_ms for point in points), dtype=np.int64)
    closes = np.fromiter((point.close for point in points), dtype=np.float64)
    highs = np.fromiter((point.high for point in points), dtype=np.float64)
    lows = np.fromiter((point.low for point in points), dtype=np.float64)
    row_count = len(points)
    latest_timestamp_ms = int(timestamps[-1])

    bad_gaps = np.diff(timestamps) != config.cadence_ms
    gap_prefix = np.concatenate(
        [np.zeros(1, dtype=np.int64), np.cumsum(bad_gaps, dtype=np.int64)]
    )
    frames: list[pd.DataFrame] = []

    for horizon_minutes in config.horizons_minutes:
        horizon_ms = horizon_minutes * MINUTE_MS
        if horizon_ms % config.cadence_ms:
            raise ValueError("horizon must align to cadence_ms")
        steps = horizon_ms // config.cadence_ms
        horizon_end = timestamps + horizon_ms
        upper_barrier = closes * (1.0 + config.upper_return)
        lower_barrier = closes * (1.0 + config.lower_return)

        max_price = np.full(row_count, np.nan, dtype=np.float64)
        min_price = np.full(row_count, np.nan, dtype=np.float64)
        labels = np.full(row_count, BarrierLabel.INCOMPLETE.value, dtype=object)
        touch_timestamp = np.full(row_count, np.nan, dtype=np.float64)
        touch_price = np.full(row_count, np.nan, dtype=np.float64)
        status = np.full(row_count, "EXCLUDED", dtype=object)
        reason = np.full(row_count, "incomplete_or_gapped_candle_path", dtype=object)

        pending = horizon_end > latest_timestamp_ms
        status[pending] = "PENDING"
        reason[pending] = "horizon_not_mature"
        _partial_extrema(
            np.flatnonzero(pending),
            timestamps,
            highs,
            lows,
            horizon_end,
            max_price,
            min_price,
        )

        window_count = row_count - steps
        if window_count > 0:
            candidate_indices = np.arange(window_count, dtype=np.int64)
            contiguous = (
                gap_prefix[candidate_indices + steps] - gap_prefix[candidate_indices] == 0
            ) & (timestamps[candidate_indices + steps] == horizon_end[candidate_indices])
            valid_indices = candidate_indices[contiguous & ~pending[:window_count]]
            high_windows = sliding_window_view(highs[1:], steps)
            low_windows = sliding_window_view(lows[1:], steps)

            for offset in range(0, len(valid_indices), chunk_rows):
                chunk = valid_indices[offset : offset + chunk_rows]
                future_highs = high_windows[chunk]
                future_lows = low_windows[chunk]
                max_price[chunk] = np.max(future_highs, axis=1)
                min_price[chunk] = np.min(future_lows, axis=1)

                upper_hits = future_highs >= upper_barrier[chunk, None]
                lower_hits = future_lows <= lower_barrier[chunk, None]
                any_upper = np.any(upper_hits, axis=1)
                any_lower = np.any(lower_hits, axis=1)
                first_upper = np.where(any_upper, np.argmax(upper_hits, axis=1), steps + 1)
                first_lower = np.where(any_lower, np.argmax(lower_hits, axis=1), steps + 1)

                upper_first = first_upper < first_lower
                lower_first = first_lower < first_upper
                ambiguous = any_upper & any_lower & (first_upper == first_lower)

                labels[chunk] = BarrierLabel.NO_EVENT.value
                status[chunk] = "FINAL"
                reason[chunk] = "neither_barrier_touched_in_complete_candle_path"

                upper_rows = chunk[upper_first]
                labels[upper_rows] = BarrierLabel.UP_10.value
                reason[upper_rows] = "upper_barrier_touched_first_by_candle_high"
                touch_price[upper_rows] = upper_barrier[upper_rows]

                lower_rows = chunk[lower_first]
                labels[lower_rows] = BarrierLabel.DOWN_10.value
                reason[lower_rows] = "lower_barrier_touched_first_by_candle_low"
                touch_price[lower_rows] = lower_barrier[lower_rows]

                ambiguous_rows = chunk[ambiguous]
                labels[ambiguous_rows] = BarrierLabel.AMBIGUOUS.value
                status[ambiguous_rows] = "EXCLUDED"
                reason[ambiguous_rows] = "both_barriers_touched_within_same_candle"

                first_touch = np.minimum(first_upper, first_lower)
                touched = first_touch <= steps
                touched_rows = chunk[touched]
                touch_timestamp[touched_rows] = timestamps[
                    touched_rows + 1 + first_touch[touched]
                ].astype(np.float64)

        frames.append(
            pd.DataFrame(
                {
                    "anchor_timestamp_ms": timestamps,
                    "anchor_price": closes,
                    "horizon_minutes": np.full(row_count, horizon_minutes, dtype=np.int16),
                    "horizon_end_ms": horizon_end,
                    "upper_barrier_price": upper_barrier,
                    "lower_barrier_price": lower_barrier,
                    "max_price": max_price,
                    "min_price": min_price,
                    "max_return": max_price / closes - 1.0,
                    "min_return": min_price / closes - 1.0,
                    "label": labels,
                    "touch_timestamp_ms": touch_timestamp,
                    "touch_price": touch_price,
                    "status": status,
                    "reason": reason,
                },
                columns=ANCHOR_COLUMNS,
            )
        )

    return pd.concat(frames, ignore_index=True)


def _row_from_index(
    *,
    points: list[CandlePoint],
    index_by_timestamp: dict[int, int],
    anchor_timestamp_ms: int,
    anchor_price: float,
    horizon_minutes: int,
    config: AnchorDatasetConfig,
    latest_timestamp_ms: int,
) -> dict[str, object]:
    horizon_ms = horizon_minutes * MINUTE_MS
    horizon_end = anchor_timestamp_ms + horizon_ms
    upper_price = anchor_price * (1.0 + config.upper_return)
    lower_price = anchor_price * (1.0 + config.lower_return)
    point_index = index_by_timestamp.get(anchor_timestamp_ms)

    def payload(
        *,
        label: BarrierLabel,
        status: str,
        reason: str,
        max_price: float | None = None,
        min_price: float | None = None,
        touch_timestamp_ms: int | None = None,
        touch_price: float | None = None,
    ) -> dict[str, object]:
        return {
            "anchor_timestamp_ms": anchor_timestamp_ms,
            "anchor_price": anchor_price,
            "horizon_minutes": horizon_minutes,
            "horizon_end_ms": horizon_end,
            "upper_barrier_price": upper_price,
            "lower_barrier_price": lower_price,
            "max_price": max_price,
            "min_price": min_price,
            "max_return": None if max_price is None else max_price / anchor_price - 1.0,
            "min_return": None if min_price is None else min_price / anchor_price - 1.0,
            "label": label.value,
            "touch_timestamp_ms": touch_timestamp_ms,
            "touch_price": touch_price,
            "status": status,
            "reason": reason,
        }

    if point_index is None:
        return payload(
            label=BarrierLabel.INCOMPLETE,
            status="EXCLUDED",
            reason="anchor_candle_missing",
        )

    steps = horizon_ms // config.cadence_ms
    path = points[point_index + 1 : min(len(points), point_index + steps + 1)]
    max_price = max((candle.high for candle in path), default=None)
    min_price = min((candle.low for candle in path), default=None)
    if latest_timestamp_ms < horizon_end:
        return payload(
            label=BarrierLabel.INCOMPLETE,
            status="PENDING",
            reason="horizon_not_mature",
            max_price=max_price,
            min_price=min_price,
        )

    result = label_first_touch_candles(
        PricePoint(anchor_timestamp_ms, anchor_price),
        path,
        BarrierConfig(
            upper_return=config.upper_return,
            lower_return=config.lower_return,
            horizon_ms=horizon_ms,
        ),
        cadence_ms=config.cadence_ms,
    )
    return payload(
        label=result.label,
        status=(
            "FINAL"
            if result.label not in {BarrierLabel.INCOMPLETE, BarrierLabel.AMBIGUOUS}
            else "EXCLUDED"
        ),
        reason=result.reason,
        max_price=max_price,
        min_price=min_price,
        touch_timestamp_ms=result.touch_timestamp_ms,
        touch_price=result.touch_price,
    )


def update_anchor_dataset_from_candles_fast(
    candles: Iterable[CandlePoint],
    store: AnchorDatasetStore,
    state_store: DatasetStateStore,
    config: AnchorDatasetConfig = AnchorDatasetConfig(),
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
) -> pd.DataFrame:
    """Build the first historical dataset vectorially, then update only the tail."""

    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    points = _normalize_candles(candles)
    existing = store.load()
    if not points:
        return existing
    latest_timestamp_ms = points[-1].timestamp_ms

    if existing.empty:
        return _save_and_advance_state(
            _initial_dataset(points, config, chunk_rows=chunk_rows),
            store=store,
            state_store=state_store,
            latest_timestamp_ms=latest_timestamp_ms,
        )

    index_by_timestamp = {point.timestamp_ms: index for index, point in enumerate(points)}
    last_anchor = int(existing["anchor_timestamp_ms"].max())
    new_rows: list[dict[str, object]] = []
    for point in points:
        if point.timestamp_ms <= last_anchor:
            continue
        for horizon in config.horizons_minutes:
            new_rows.append(
                _row_from_index(
                    points=points,
                    index_by_timestamp=index_by_timestamp,
                    anchor_timestamp_ms=point.timestamp_ms,
                    anchor_price=point.close,
                    horizon_minutes=horizon,
                    config=config,
                    latest_timestamp_ms=latest_timestamp_ms,
                )
            )

    combined = existing.copy()
    if new_rows:
        combined = pd.concat(
            [combined, pd.DataFrame(new_rows, columns=ANCHOR_COLUMNS)], ignore_index=True
        )

    pending = (combined["status"] == "PENDING") & (
        combined["horizon_end_ms"] <= latest_timestamp_ms
    )
    for row_index in combined.index[pending]:
        replacement = _row_from_index(
            points=points,
            index_by_timestamp=index_by_timestamp,
            anchor_timestamp_ms=int(combined.at[row_index, "anchor_timestamp_ms"]),
            anchor_price=float(combined.at[row_index, "anchor_price"]),
            horizon_minutes=int(combined.at[row_index, "horizon_minutes"]),
            config=config,
            latest_timestamp_ms=latest_timestamp_ms,
        )
        for key, value in replacement.items():
            combined.at[row_index, key] = value

    return _save_and_advance_state(
        combined,
        store=store,
        state_store=state_store,
        latest_timestamp_ms=latest_timestamp_ms,
    )
