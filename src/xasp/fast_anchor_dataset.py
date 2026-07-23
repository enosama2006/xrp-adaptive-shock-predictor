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

            for chunk_start in range(0, len(valid_indices), chunk_rows):
                chunk_indices = valid_indices[chunk_start : chunk_start + chunk_rows]
                if len(chunk_indices) == 0:
                    continue
                chunk_highs = high_windows[chunk_indices]
                chunk_lows = low_windows[chunk_indices]
                max_price[chunk_indices] = np.max(chunk_highs, axis=1)
                min_price[chunk_indices] = np.min(chunk_lows, axis=1)

                upper_hits = chunk_highs >= upper_barrier[chunk_indices, None]
                lower_hits = chunk_lows <= lower_barrier[chunk_indices, None]
                upper_any = upper_hits.any(axis=1)
                lower_any = lower_hits.any(axis=1)
                upper_first = np.where(upper_any, upper_hits.argmax(axis=1), steps + 1)
                lower_first = np.where(lower_any, lower_hits.argmax(axis=1), steps + 1)

                no_event = ~upper_any & ~lower_any
                ambiguous = upper_any & lower_any & (upper_first == lower_first)
                up_first = upper_any & (upper_first < lower_first)
                down_first = lower_any & (lower_first < upper_first)

                labels[chunk_indices[no_event]] = BarrierLabel.NO_EVENT.value
                labels[chunk_indices[ambiguous]] = BarrierLabel.AMBIGUOUS.value
                labels[chunk_indices[up_first]] = BarrierLabel.UP_10.value
                labels[chunk_indices[down_first]] = BarrierLabel.DOWN_10.value
                status[chunk_indices[no_event | up_first | down_first]] = "FINAL"
                status[chunk_indices[ambiguous]] = "EXCLUDED"
                reason[chunk_indices[no_event]] = "no_barrier_touched_within_horizon"
                reason[chunk_indices[ambiguous]] = "both_barriers_touched_in_same_candle"
                reason[chunk_indices[up_first]] = "upper_barrier_touched_first_by_candle_high"
                reason[chunk_indices[down_first]] = "lower_barrier_touched_first_by_candle_low"

                up_rows = chunk_indices[up_first]
                if len(up_rows):
                    up_steps = upper_first[up_first] + 1
                    touch_timestamp[up_rows] = timestamps[up_rows + up_steps]
                    touch_price[up_rows] = upper_barrier[up_rows]
                down_rows = chunk_indices[down_first]
                if len(down_rows):
                    down_steps = lower_first[down_first] + 1
                    touch_timestamp[down_rows] = timestamps[down_rows + down_steps]
                    touch_price[down_rows] = lower_barrier[down_rows]

        max_return = max_price / closes - 1.0
        min_return = min_price / closes - 1.0
        frame = pd.DataFrame(
            {
                "anchor_timestamp_ms": timestamps,
                "anchor_price": closes,
                "horizon_minutes": horizon_minutes,
                "horizon_end_ms": horizon_end,
                "upper_barrier_price": upper_barrier,
                "lower_barrier_price": lower_barrier,
                "max_price": max_price,
                "min_price": min_price,
                "max_return": max_return,
                "min_return": min_return,
                "label": labels,
                "touch_timestamp_ms": touch_timestamp,
                "touch_price": touch_price,
                "status": status,
                "reason": reason,
            },
            columns=ANCHOR_COLUMNS,
        )
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=ANCHOR_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True
    )


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
    start_index = index_by_timestamp.get(anchor_timestamp_ms)
    if start_index is None:
        return {
            "anchor_timestamp_ms": anchor_timestamp_ms,
            "anchor_price": anchor_price,
            "horizon_minutes": horizon_minutes,
            "horizon_end_ms": anchor_timestamp_ms + horizon_ms,
            "upper_barrier_price": anchor_price * (1.0 + config.upper_return),
            "lower_barrier_price": anchor_price * (1.0 + config.lower_return),
            "max_price": None,
            "min_price": None,
            "max_return": None,
            "min_return": None,
            "label": BarrierLabel.INCOMPLETE.value,
            "touch_timestamp_ms": None,
            "touch_price": None,
            "status": "EXCLUDED",
            "reason": "anchor_candle_missing_from_current_dataset",
        }
    anchor = PricePoint(anchor_timestamp_ms, anchor_price)
    result = label_first_touch_candles(
        anchor,
        points[start_index + 1 :],
        BarrierConfig(
            upper_return=config.upper_return,
            lower_return=config.lower_return,
            horizon_ms=horizon_ms,
        ),
        cadence_ms=config.cadence_ms,
    )
    horizon_end_ms = anchor_timestamp_ms + horizon_ms
    status = (
        "PENDING"
        if horizon_end_ms > latest_timestamp_ms
        else "FINAL"
        if result.label not in {BarrierLabel.INCOMPLETE, BarrierLabel.AMBIGUOUS}
        else "EXCLUDED"
    )
    return {
        "anchor_timestamp_ms": anchor_timestamp_ms,
        "anchor_price": anchor_price,
        "horizon_minutes": horizon_minutes,
        "horizon_end_ms": horizon_end_ms,
        "upper_barrier_price": anchor_price * (1.0 + config.upper_return),
        "lower_barrier_price": anchor_price * (1.0 + config.lower_return),
        "max_price": (
            None
            if result.max_favorable_excursion is None
            else anchor_price * (1.0 + result.max_favorable_excursion)
        ),
        "min_price": (
            None
            if result.max_adverse_excursion is None
            else anchor_price * (1.0 + result.max_adverse_excursion)
        ),
        "max_return": result.max_favorable_excursion,
        "min_return": result.max_adverse_excursion,
        "label": result.label.value,
        "touch_timestamp_ms": result.touch_timestamp_ms,
        "touch_price": result.touch_price,
        "status": status,
        "reason": result.reason,
    }


def _append_anchor_rows_without_all_na_concat_warning(
    existing: pd.DataFrame,
    rows: list[dict[str, object]],
) -> pd.DataFrame:
    """Append rows while making dtype resolution explicit for all-NA columns.

    Pandas 2.x warns that concatenating an all-NA incoming column will change
    dtype inference in a future release. All-NA incoming columns carry no dtype
    information, so omit them from the concat input and restore the governed
    schema afterwards. The values for the appended rows remain missing.
    """

    incoming = pd.DataFrame(rows, columns=ANCHOR_COLUMNS)
    informative_columns = [
        column for column in ANCHOR_COLUMNS if not incoming[column].isna().all()
    ]
    combined = pd.concat(
        [existing, incoming[informative_columns]],
        ignore_index=True,
        sort=False,
    )
    return combined.reindex(columns=ANCHOR_COLUMNS)


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
        combined = _append_anchor_rows_without_all_na_concat_warning(combined, new_rows)

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
