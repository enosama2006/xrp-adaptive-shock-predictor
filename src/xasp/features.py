"""Leakage-aware feature construction from minute-level observed market data."""

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
    quote_volume_column: str = "quote_volume"
    trade_count_column: str = "trade_count"
    taker_buy_base_column: str = "taker_buy_base"
    taker_buy_quote_column: str = "taker_buy_quote"


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


def _numeric_optional(frame: pd.DataFrame, name: str) -> pd.Series | None:
    if name not in frame.columns:
        return None
    return pd.to_numeric(frame[name], errors="coerce").astype(float)


def _validate_non_negative(values: pd.Series, name: str) -> None:
    if (values.dropna() < 0).any():
        raise ValueError(f"{name} must be non-negative")


def _add_normalized_family(
    frame: pd.DataFrame,
    *,
    values: pd.Series,
    base_name: str,
    windows: tuple[int, ...],
    robust: bool = True,
) -> None:
    for window in windows:
        frame[f"{base_name}_zscore_{window}m"] = _safe_zscore(values, window)
        if robust:
            frame[f"{base_name}_robust_zscore_{window}m"] = _safe_robust_zscore(
                values,
                window,
            )


def build_price_features(
    prices: pd.DataFrame,
    config: FeatureConfig = FeatureConfig(),
) -> pd.DataFrame:
    """Build causal, scale-stable price, volume, and kline trade-flow features.

    Raw exchange values retain full precision in the source table. The returned
    model-feature table keeps the reference price for display/join purposes but
    derives learnable inputs from relative/log/standardized quantities. All
    rolling statistics use only the current and prior completed candles.
    """

    _require_columns(prices, {config.timestamp_column, config.price_column})
    optional_names = (
        config.volume_column,
        config.quote_volume_column,
        config.trade_count_column,
        config.taker_buy_base_column,
        config.taker_buy_quote_column,
    )
    optional = [name for name in optional_names if name in prices.columns]
    frame = prices[[config.timestamp_column, config.price_column, *optional]].copy()
    frame = frame.drop_duplicates(config.timestamp_column, keep="last")
    frame = frame.sort_values(config.timestamp_column, ignore_index=True)

    if frame.empty:
        return frame
    if (pd.to_numeric(frame[config.price_column], errors="coerce") <= 0).any():
        raise ValueError("prices must be positive")
    if not frame[config.timestamp_column].is_monotonic_increasing:
        raise ValueError("timestamps must be monotonic")

    price = pd.to_numeric(frame[config.price_column], errors="coerce").astype(float)
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
        frame[f"range_position_{window}m"] = (
            (price - rolling_low) / spread
        ).fillna(0.5)
        frame[f"drawdown_{window}m"] = (price / rolling_high) - 1
        frame[f"distance_from_low_{window}m"] = (price / rolling_low) - 1
        frame[f"distance_from_high_{window}m"] = (price / rolling_high) - 1

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

    volume = _numeric_optional(frame, config.volume_column)
    quote_volume = _numeric_optional(frame, config.quote_volume_column)
    trade_count = _numeric_optional(frame, config.trade_count_column)
    taker_buy_base = _numeric_optional(frame, config.taker_buy_base_column)
    taker_buy_quote = _numeric_optional(frame, config.taker_buy_quote_column)

    if volume is not None:
        _validate_non_negative(volume, config.volume_column)
        log_volume = np.log1p(volume)
        frame["log1p_volume"] = log_volume
        _add_normalized_family(
            frame,
            values=log_volume,
            base_name="volume",
            windows=config.normalization_windows_minutes,
        )
        frame["availability_volume"] = volume.notna().astype(float)

    if quote_volume is not None:
        _validate_non_negative(quote_volume, config.quote_volume_column)
        log_quote_volume = np.log1p(quote_volume)
        frame["log1p_quote_volume"] = log_quote_volume
        _add_normalized_family(
            frame,
            values=log_quote_volume,
            base_name="quote_volume",
            windows=config.normalization_windows_minutes,
        )
        frame["availability_quote_volume"] = quote_volume.notna().astype(float)

    if trade_count is not None:
        _validate_non_negative(trade_count, config.trade_count_column)
        trade_intensity_log = np.log1p(trade_count)
        frame["trade_intensity_log1p"] = trade_intensity_log
        _add_normalized_family(
            frame,
            values=trade_intensity_log,
            base_name="trade_intensity",
            windows=config.normalization_windows_minutes,
            robust=False,
        )
        frame["availability_trade_count"] = trade_count.notna().astype(float)

    if volume is not None and trade_count is not None:
        average_trade_size = volume.div(trade_count.replace(0, np.nan))
        average_trade_size_log = np.log1p(average_trade_size)
        frame["average_trade_size_log1p"] = average_trade_size_log
        _add_normalized_family(
            frame,
            values=average_trade_size_log,
            base_name="average_trade_size",
            windows=config.normalization_windows_minutes,
            robust=False,
        )

    if volume is not None and taker_buy_base is not None:
        _validate_non_negative(taker_buy_base, config.taker_buy_base_column)
        invalid = (taker_buy_base > volume) & volume.notna() & taker_buy_base.notna()
        if invalid.any():
            raise ValueError("taker_buy_base cannot exceed total volume")
        taker_buy_ratio = taker_buy_base.div(volume.replace(0, np.nan))
        signed_volume_ratio = taker_buy_ratio.mul(2.0).sub(1.0)
        frame["taker_buy_ratio"] = taker_buy_ratio
        frame["signed_volume_ratio"] = signed_volume_ratio
        for window in config.normalization_windows_minutes:
            frame[f"taker_buy_ratio_zscore_{window}m"] = _safe_zscore(
                taker_buy_ratio,
                window,
            )
            frame[f"signed_volume_zscore_{window}m"] = _safe_zscore(
                signed_volume_ratio,
                window,
            )
        frame["availability_taker_flow"] = (
            volume.notna() & taker_buy_base.notna()
        ).astype(float)

    if taker_buy_quote is not None:
        _validate_non_negative(taker_buy_quote, config.taker_buy_quote_column)
        frame["availability_taker_buy_quote"] = taker_buy_quote.notna().astype(float)

    raw_optional = [name for name in optional_names if name in frame.columns]
    if raw_optional:
        frame = frame.drop(columns=raw_optional)

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
        values = series.to_numpy(dtype=float, na_value=np.nan)
        finite = series[np.isfinite(values)].astype(float)
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
        quantile_payload = {str(key): float(value) for key, value in quantiles.items()}
        payload.update(
            {
                "status": "OK",
                "mean": float(finite.mean()),
                "std": float(finite.std(ddof=0)),
                "median": float(finite.median()),
                "iqr": float(quantiles.loc[0.75] - quantiles.loc[0.25]),
                "skewness": float(finite.skew()) if len(finite) >= 3 else None,
                "quantiles": quantile_payload,
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
