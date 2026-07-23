"""Vectorized future-excursion target construction for Model A."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .future_envelope import HORIZONS

MINUTE_MS = 60_000
DEFAULT_CHUNK_ROWS = 100_000


def build_future_envelope_targets_fast(
    prices: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
) -> pd.DataFrame:
    """Create complete observed future-extrema targets without per-anchor scans."""

    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    required = {"timestamp_ms", "price"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price dataset missing columns: {sorted(missing)}")

    selected = ["timestamp_ms", "price"]
    for optional in ("high", "low"):
        if optional in prices.columns:
            selected.append(optional)
    frame = prices[selected].drop_duplicates("timestamp_ms", keep="last")
    frame = frame.sort_values("timestamp_ms", ignore_index=True)
    if frame.empty:
        return pd.DataFrame()

    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    if frame["price"].isna().any() or (frame["price"] <= 0).any():
        raise ValueError("prices must be finite and positive")
    frame["high"] = frame["high"] if "high" in frame else frame["price"]
    frame["low"] = frame["low"] if "low" in frame else frame["price"]
    if (frame["high"] < frame["low"]).any():
        raise ValueError("candle high must be greater than or equal to low")
    if ((frame["price"] > frame["high"]) | (frame["price"] < frame["low"])).any():
        raise ValueError("close price must lie inside candle high/low")

    timestamps = frame["timestamp_ms"].to_numpy(dtype=np.int64)
    closes = frame["price"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    bad_gaps = np.diff(timestamps) != MINUTE_MS
    gap_prefix = np.concatenate(
        [np.zeros(1, dtype=np.int64), np.cumsum(bad_gaps, dtype=np.int64)]
    )
    output: list[pd.DataFrame] = []

    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("horizons must be positive")
        steps = int(horizon)
        window_count = len(frame) - steps
        if window_count <= 0:
            continue

        anchor_indices = np.arange(window_count, dtype=np.int64)
        horizon_end = timestamps[anchor_indices] + steps * MINUTE_MS
        contiguous = (
            gap_prefix[anchor_indices + steps] - gap_prefix[anchor_indices] == 0
        ) & (timestamps[anchor_indices + steps] == horizon_end)
        valid_indices = anchor_indices[contiguous]
        if len(valid_indices) == 0:
            continue

        high_windows = sliding_window_view(highs[1:], steps)
        low_windows = sliding_window_view(lows[1:], steps)
        for offset in range(0, len(valid_indices), chunk_rows):
            chunk = valid_indices[offset : offset + chunk_rows]
            future_highs = high_windows[chunk]
            future_lows = low_windows[chunk]
            max_offsets = np.argmax(future_highs, axis=1) + 1
            min_offsets = np.argmin(future_lows, axis=1) + 1
            max_price = np.max(future_highs, axis=1)
            min_price = np.min(future_lows, axis=1)
            anchor_price = closes[chunk]

            rows: dict[str, Any] = {
                "anchor_timestamp_ms": timestamps[chunk],
                "anchor_price": anchor_price,
                "horizon_minutes": np.full(len(chunk), horizon, dtype=np.int16),
                "horizon_end_ms": timestamps[chunk] + horizon * MINUTE_MS,
                "future_max_price": max_price,
                "future_min_price": min_price,
                "future_max_return": max_price / anchor_price - 1.0,
                "future_min_return": min_price / anchor_price - 1.0,
                "minutes_to_max": max_offsets.astype(np.int16),
                "minutes_to_min": min_offsets.astype(np.int16),
                "hit_up_02": max_price >= anchor_price * 1.02,
                "hit_up_05": max_price >= anchor_price * 1.05,
                "hit_up_10": max_price >= anchor_price * 1.10,
                "hit_down_02": min_price <= anchor_price * 0.98,
                "hit_down_05": min_price <= anchor_price * 0.95,
                "hit_down_10": min_price <= anchor_price * 0.90,
                "status": np.full(len(chunk), "FINAL", dtype=object),
            }
            output.append(pd.DataFrame(rows))

    if not output:
        return pd.DataFrame()
    return pd.concat(output, ignore_index=True).sort_values(
        ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True
    )
