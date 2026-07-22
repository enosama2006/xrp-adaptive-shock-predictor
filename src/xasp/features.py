"""Leakage-aware feature construction from minute-level market data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """Configuration for deterministic backward-looking features."""

    price_column: str = "price"
    timestamp_column: str = "timestamp_ms"
    windows_minutes: tuple[int, ...] = (1, 5, 15, 30, 60)


def _require_columns(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")


def build_price_features(
    prices: pd.DataFrame,
    config: FeatureConfig = FeatureConfig(),
) -> pd.DataFrame:
    """Build causal price features using current and strictly prior rows only.

    Input must contain one row per minute, sorted or sortable by timestamp. The
    output includes the original timestamp and price plus rolling returns,
    realized volatility, range position, momentum acceleration, and drawdown.
    No forward shift or future aggregation is used.
    """

    _require_columns(prices, {config.timestamp_column, config.price_column})
    frame = prices[[config.timestamp_column, config.price_column]].copy()
    frame = frame.drop_duplicates(config.timestamp_column, keep="last")
    frame = frame.sort_values(config.timestamp_column, ignore_index=True)

    if frame.empty:
        return frame
    if (frame[config.price_column] <= 0).any():
        raise ValueError("prices must be positive")
    if not frame[config.timestamp_column].is_monotonic_increasing:
        raise ValueError("timestamps must be monotonic")

    price = frame[config.price_column].astype(float)
    one_step_return = price.pct_change()
    frame["return_1m"] = one_step_return

    for window in config.windows_minutes:
        if window <= 0:
            raise ValueError("feature windows must be positive")
        frame[f"return_{window}m"] = price.pct_change(window)
        frame[f"volatility_{window}m"] = one_step_return.rolling(
            window=window,
            min_periods=max(2, min(window, 3)),
        ).std(ddof=0)
        rolling_high = price.rolling(window=window, min_periods=1).max()
        rolling_low = price.rolling(window=window, min_periods=1).min()
        spread = (rolling_high - rolling_low).replace(0, np.nan)
        frame[f"range_position_{window}m"] = ((price - rolling_low) / spread).fillna(0.5)
        frame[f"drawdown_{window}m"] = (price / rolling_high) - 1

    frame["momentum_acceleration_5m"] = frame["return_1m"] - frame["return_5m"].div(5)
    frame["jump_score_15m"] = frame["return_1m"].abs().div(
        frame["volatility_15m"].replace(0, np.nan)
    )
    frame["feature_available_at_ms"] = frame[config.timestamp_column]
    return frame.replace([np.inf, -np.inf], np.nan)


def join_anchors_with_features(
    anchors: pd.DataFrame,
    features: pd.DataFrame,
) -> pd.DataFrame:
    """Join features to anchors at the same timestamp and enforce availability."""

    _require_columns(anchors, {"anchor_timestamp_ms"})
    _require_columns(features, {"timestamp_ms", "feature_available_at_ms"})
    merged = anchors.merge(
        features,
        left_on="anchor_timestamp_ms",
        right_on="timestamp_ms",
        how="left",
        validate="many_to_one",
    )
    leaked = merged["feature_available_at_ms"].notna() & (
        merged["feature_available_at_ms"] > merged["anchor_timestamp_ms"]
    )
    if leaked.any():
        raise ValueError("feature availability exceeds anchor timestamp")
    return merged
