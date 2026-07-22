"""Leakage-aware feature construction from minute-level market data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """Configuration for deterministic backward-looking features."""

    price_column: str = "price"
    timestamp_column: str = "timestamp_ms"
    windows_minutes: tuple[int, ...] = (1, 5, 15, 30, 60)
    normalization_windows_minutes: tuple[int, ...] = (15, 60, 240)
    volume_column: str = "volume"


def _require_columns(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")


def _safe_zscore(values: pd.Series, window: int) -> pd.Series:
    minimum = min(window, max(2, window // 4))
    mean = values.rolling(window=window, min_periods=minimum).mean()
    std = values.rolling(window=window, min_periods=minimum).std(ddof=0)
    return (values - mean).div(std.replace(0, np.nan))


def _safe_robust_zscore(values: pd.Series, window: int) -> pd.Series:
    minimum = min(window, max(2, window // 4))
    rolling = values.rolling(window=window, min_periods=minimum)
    median = rolling.median()
    q25 = rolling.quantile(0.25)
    q75 = rolling.quantile(0.75)
    iqr = (q75 - q25).replace(0, np.nan)
    return (values - median).div(iqr)


def build_price_features(
    prices: pd.DataFrame,
    config: FeatureConfig = FeatureConfig(),
) -> pd.DataFrame:
    """Build causal, scale-stable features from current and prior observations only.

    Raw prices retain exchange precision for auditability, but model inputs are
    predominantly relative returns, log returns, rolling location, volatility,
    and rolling standardized values. Rolling statistics never use future rows.
    Optional volume features are log-compressed and standardized causally.
    """

    _require_columns(prices, {config.timestamp_column, config.price_column})
    optional = [name for name in (config.volume_column,) if name in prices.columns]
    frame = prices[[config.timestamp_column, config.price_column, *optional]].copy()
    frame = frame.drop_duplicates(config.timestamp_column, keep="last")
    frame = frame.sort_values(config.timestamp_column, ignore_index=True)

    if frame.empty:
        return frame
    if (frame[config.price_column] <= 0).any():
        raise ValueError("prices must be positive")
    if not frame[config.timestamp_column].is_monotonic_increasing:
        raise ValueError("timestamps must be monotonic")

    price = frame[config.price_column].astype(float)
    log_price = np.log(price)
    one_step_return = price.pct_change()
    one_step_log_return = log_price.diff()
    frame["return_1m"] = one_step_return
    frame["log_return_1m"] = one_step_log_return

    for window in config.windows_minutes:
        if window <= 0:
            raise ValueError("feature windows must be positive")
        frame[f"return_{window}m"] = price.pct_change(window)
        frame[f"log_return_{window}m"] = log_price.diff(window)
        volatility_min_periods = min(window, 2)
        frame[f"volatility_{window}m"] = one_step_log_return.rolling(
            window=window,
            min_periods=volatility_min_periods,
        ).std(ddof=0)
        rolling_high = price.rolling(window=window, min_periods=1).max()
        rolling_low = price.rolling(window=window, min_periods=1).min()
        spread = (rolling_high - rolling_low).replace(0, np.nan)
        frame[f"range_position_{window}m"] = ((price - rolling_low) / spread).fillna(0.5)
        frame[f"drawdown_{window}m"] = (price / rolling_high) - 1
        frame[f"distance_from_low_{window}m"] = (price / rolling_low) - 1

    for window in config.normalization_windows_minutes:
        if window <= 1:
            raise ValueError("normalization windows must be greater than one")
        frame[f"price_zscore_{window}m"] = _safe_zscore(log_price, window)
        frame[f"return_robust_zscore_{window}m"] = _safe_robust_zscore(
            one_step_log_return,
            window,
        )

    frame["momentum_acceleration_5m"] = frame["log_return_1m"] - frame[
        "log_return_5m"
    ].div(5)
    frame["jump_score_15m"] = frame["log_return_1m"].abs().div(
        frame["volatility_15m"].replace(0, np.nan)
    )

    if config.volume_column in frame.columns:
        volume = frame[config.volume_column].astype(float)
        if (volume < 0).any():
            raise ValueError("volume must be non-negative")
        log_volume = np.log1p(volume)
        frame["log1p_volume"] = log_volume
        for window in config.normalization_windows_minutes:
            frame[f"volume_zscore_{window}m"] = _safe_zscore(log_volume, window)
            frame[f"volume_robust_zscore_{window}m"] = _safe_robust_zscore(
                log_volume,
                window,
            )
        frame = frame.drop(columns=[config.volume_column])

    frame["feature_available_at_ms"] = frame[config.timestamp_column]
    return frame.replace([np.inf, -np.inf], np.nan)


def build_feature_diagnostics(
    features: pd.DataFrame,
    excluded: set[str] | None = None,
    histogram_bins: int = 20,
) -> dict[str, Any]:
    """Summarize feature scale, skew, missingness, quantiles, and histograms.

    Diagnostics are descriptive only and must not be used to fit transformations
    on future/test data. Model pipelines remain responsible for fitting any
    learned scaling exclusively on the training partition.
    """

    if histogram_bins < 2:
        raise ValueError("histogram_bins must be at least two")
    ignored = excluded or {"timestamp_ms", "price", "feature_available_at_ms"}
    report: dict[str, Any] = {"row_count": int(len(features)), "features": {}}
    for name in features.columns:
        if name in ignored or not pd.api.types.is_numeric_dtype(features[name]):
            continue
        series = pd.to_numeric(features[name], errors="coerce")
        finite = series[np.isfinite(series.to_numpy(dtype=float, na_value=np.nan))].astype(float)
        payload: dict[str, Any] = {
            "non_null": int(series.notna().sum()),
            "missing_fraction": float(series.isna().mean()),
        }
        if finite.empty:
            payload["status"] = "NO_FINITE_VALUES"
            report["features"][name] = payload
            continue
        counts, edges = np.histogram(finite.to_numpy(), bins=histogram_bins)
        quantiles = finite.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99])
        payload.update(
            {
                "status": "OK",
                "mean": float(finite.mean()),
                "std": float(finite.std(ddof=0)),
                "median": float(finite.median()),
                "iqr": float(quantiles.loc[0.75] - quantiles.loc[0.25]),
                "skewness": float(finite.skew()) if len(finite) >= 3 else None,
                "quantiles": {str(key): float(value) for key, value in quantiles.items()},
                "histogram": {
                    "counts": [int(value) for value in counts],
                    "edges": [float(value) for value in edges],
                },
            }
        )
        report["features"][name] = payload
    return report


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
