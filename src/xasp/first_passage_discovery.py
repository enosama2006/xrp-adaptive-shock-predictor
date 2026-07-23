"""Empirical discovery of XRP ±10% first-passage windows.

This module is deliberately separate from Model B training. It studies observed
completed candles first, measures exact minute-level barrier passage times from
hourly sampled anchors, tests return normality, and reports event rates across
windows through fourteen days. The report is descriptive evidence only; it
never emits a trading decision or fabricates unavailable market-cap data.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .price_store import PartitionedPriceStore, PriceStoreStats

MINUTE_MS = 60_000
DISCOVERY_SCHEMA_VERSION = 1
DEFAULT_DISCOVERY_HORIZONS_MINUTES = (
    15,
    30,
    45,
    60,
    120,
    180,
    240,
    480,
    720,
    1_440,
    2_880,
    4_320,
    10_080,
    20_160,
)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    horizons_minutes: tuple[int, ...] = DEFAULT_DISCOVERY_HORIZONS_MINUTES
    threshold_return: float = 0.10
    anchor_stride_minutes: int = 60
    volatility_window_minutes: int = 1_440
    batch_rows: int = 128
    independent_cluster_separation_minutes: int = 1_440

    def __post_init__(self) -> None:
        if not self.horizons_minutes:
            raise ValueError("horizons_minutes cannot be empty")
        if tuple(sorted(set(self.horizons_minutes))) != self.horizons_minutes:
            raise ValueError("horizons_minutes must be unique and sorted")
        if any(value <= 0 for value in self.horizons_minutes):
            raise ValueError("horizons_minutes must contain positive values")
        if not 0.0 < self.threshold_return < 1.0:
            raise ValueError("threshold_return must be in (0, 1)")
        if self.anchor_stride_minutes < 1:
            raise ValueError("anchor_stride_minutes must be positive")
        if self.volatility_window_minutes < 2:
            raise ValueError("volatility_window_minutes must be at least two")
        if self.batch_rows < 1:
            raise ValueError("batch_rows must be positive")
        if self.independent_cluster_separation_minutes < 1:
            raise ValueError("independent_cluster_separation_minutes must be positive")

    @property
    def max_horizon_minutes(self) -> int:
        return max(self.horizons_minutes)


@dataclass(frozen=True, slots=True)
class DiscoveryCacheIdentity:
    schema_version: int
    price_rows: int
    data_start_ms: int | None
    data_end_ms: int | None
    threshold_return: float
    anchor_stride_minutes: int
    horizons_minutes: tuple[int, ...]


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _cache_identity(stats: PriceStoreStats, config: DiscoveryConfig) -> DiscoveryCacheIdentity:
    return DiscoveryCacheIdentity(
        schema_version=DISCOVERY_SCHEMA_VERSION,
        price_rows=stats.total_rows,
        data_start_ms=stats.min_timestamp_ms,
        data_end_ms=stats.max_timestamp_ms,
        threshold_return=config.threshold_return,
        anchor_stride_minutes=config.anchor_stride_minutes,
        horizons_minutes=config.horizons_minutes,
    )


def _cached_report_is_fresh(
    report: dict[str, Any],
    identity: DiscoveryCacheIdentity,
    *,
    refresh_after_new_rows: int,
) -> bool:
    metadata = report.get("cache_identity")
    if not isinstance(metadata, dict):
        return False
    if int(metadata.get("schema_version", -1)) != identity.schema_version:
        return False
    if metadata.get("data_start_ms") != identity.data_start_ms:
        return False
    if float(metadata.get("threshold_return", -1.0)) != identity.threshold_return:
        return False
    if int(metadata.get("anchor_stride_minutes", -1)) != identity.anchor_stride_minutes:
        return False
    if tuple(metadata.get("horizons_minutes", ())) != identity.horizons_minutes:
        return False
    previous_rows = int(metadata.get("price_rows", 0))
    return 0 <= identity.price_rows - previous_rows < refresh_after_new_rows


def _cluster_count(timestamps_ms: np.ndarray, separation_minutes: int) -> int:
    if timestamps_ms.size == 0:
        return 0
    unique = np.unique(timestamps_ms.astype(np.int64, copy=False))
    if unique.size == 0:
        return 0
    separation_ms = separation_minutes * MINUTE_MS
    return int(1 + np.sum(np.diff(unique) > separation_ms))


def _mode_minutes(values: np.ndarray) -> tuple[int | None, int]:
    if values.size == 0:
        return None, 0
    unique, counts = np.unique(values.astype(np.int64, copy=False), return_counts=True)
    index = int(np.argmax(counts))
    return int(unique[index]), int(counts[index])


def _time_statistics(
    steps: np.ndarray,
    *,
    max_horizon_minutes: int,
    valid_anchor_count: int,
) -> dict[str, Any]:
    observed = steps[steps > 0].astype(np.float64, copy=False)
    mode, mode_frequency = _mode_minutes(observed)
    censored = np.where(steps > 0, steps, max_horizon_minutes).astype(np.float64)
    if observed.size == 0:
        return {
            "observed_events": 0,
            "hit_rate": 0.0,
            "conditional_mean_minutes": None,
            "restricted_mean_minutes": float(censored.mean()) if censored.size else None,
            "median_minutes": None,
            "mode_minutes": None,
            "mode_frequency": 0,
            "standard_deviation_minutes": None,
            "minimum_minutes": None,
            "maximum_minutes": None,
            "p25_minutes": None,
            "p75_minutes": None,
            "p90_minutes": None,
            "p95_minutes": None,
        }
    quantiles = np.quantile(observed, [0.25, 0.50, 0.75, 0.90, 0.95])
    return {
        "observed_events": int(observed.size),
        "hit_rate": float(observed.size / max(valid_anchor_count, 1)),
        "conditional_mean_minutes": float(observed.mean()),
        "restricted_mean_minutes": float(censored.mean()),
        "median_minutes": float(quantiles[1]),
        "mode_minutes": mode,
        "mode_frequency": mode_frequency,
        "standard_deviation_minutes": float(observed.std(ddof=0)),
        "minimum_minutes": int(observed.min()),
        "maximum_minutes": int(observed.max()),
        "p25_minutes": float(quantiles[0]),
        "p75_minutes": float(quantiles[2]),
        "p90_minutes": float(quantiles[3]),
        "p95_minutes": float(quantiles[4]),
    }


def _return_distribution_diagnostics(closes: np.ndarray) -> dict[str, Any]:
    log_returns = np.diff(np.log(closes))
    log_returns = log_returns[np.isfinite(log_returns)]
    if log_returns.size < 3:
        return {"status": "WAIT", "reason": "insufficient_returns"}
    series = pd.Series(log_returns)
    mean = float(series.mean())
    standard_deviation = float(series.std(ddof=0))
    median = float(series.median())
    mad = float(np.median(np.abs(log_returns - median)))
    diagnostics: dict[str, Any] = {
        "status": "READY",
        "observations": int(log_returns.size),
        "mean_log_return": mean,
        "median_log_return": median,
        "standard_deviation": standard_deviation,
        "median_absolute_deviation": mad,
        "skewness": float(series.skew()),
        "excess_kurtosis": float(series.kurt()),
        "minimum_log_return": float(log_returns.min()),
        "maximum_log_return": float(log_returns.max()),
        "quantiles": {
            "p001": float(np.quantile(log_returns, 0.001)),
            "p01": float(np.quantile(log_returns, 0.01)),
            "p05": float(np.quantile(log_returns, 0.05)),
            "p95": float(np.quantile(log_returns, 0.95)),
            "p99": float(np.quantile(log_returns, 0.99)),
            "p999": float(np.quantile(log_returns, 0.999)),
        },
        "normality_note": (
            "Observed tail rates are compared with a Gaussian reference only; "
            "the model does not assume crypto returns are normal."
        ),
    }
    tail_rates: dict[str, Any] = {}
    if standard_deviation > 0.0:
        z = np.abs((log_returns - mean) / standard_deviation)
        for sigma in (3, 4, 5):
            observed_rate = float(np.mean(z >= sigma))
            normal_rate = float(math.erfc(sigma / math.sqrt(2.0)))
            tail_rates[str(sigma)] = {
                "observed_count": int(np.sum(z >= sigma)),
                "observed_rate": observed_rate,
                "normal_reference_rate": normal_rate,
                "observed_to_normal_ratio": (
                    None if normal_rate == 0.0 else observed_rate / normal_rate
                ),
            }
    diagnostics["absolute_sigma_tail_rates"] = tail_rates
    if mad > 0.0:
        robust_z = np.abs((log_returns - median) / (1.4826 * mad))
        diagnostics["robust_outliers"] = {
            "absolute_robust_z_ge_6_count": int(np.sum(robust_z >= 6.0)),
            "absolute_robust_z_ge_6_rate": float(np.mean(robust_z >= 6.0)),
        }
    return diagnostics


def _regime_labels(
    closes: np.ndarray,
    anchor_indices: np.ndarray,
    window_minutes: int,
) -> tuple[np.ndarray, dict[str, float]]:
    returns = np.full(closes.size, np.nan, dtype=np.float64)
    returns[1:] = np.diff(np.log(closes))
    rolling = (
        pd.Series(returns)
        .rolling(window_minutes, min_periods=window_minutes)
        .std(ddof=0)
        .to_numpy(dtype=np.float64)
    )
    sampled = rolling[anchor_indices]
    finite = sampled[np.isfinite(sampled)]
    labels = np.full(sampled.size, "UNKNOWN", dtype=object)
    if finite.size < 3:
        return labels, {"low_medium": float("nan"), "medium_high": float("nan")}
    low_medium, medium_high = np.quantile(finite, [1.0 / 3.0, 2.0 / 3.0])
    labels[np.isfinite(sampled) & (sampled <= low_medium)] = "LOW"
    labels[
        np.isfinite(sampled) & (sampled > low_medium) & (sampled <= medium_high)
    ] = "MEDIUM"
    labels[np.isfinite(sampled) & (sampled > medium_high)] = "HIGH"
    return labels, {
        "low_medium": float(low_medium),
        "medium_high": float(medium_high),
    }


def _empirical_excursion_summary(max_returns: np.ndarray, min_returns: np.ndarray) -> dict[str, Any]:
    return {
        "sample_rows": int(max_returns.size),
        "max_return_mean": float(max_returns.mean()),
        "max_return_q05": float(np.quantile(max_returns, 0.05)),
        "max_return_q50": float(np.quantile(max_returns, 0.50)),
        "max_return_q95": float(np.quantile(max_returns, 0.95)),
        "min_return_mean": float(min_returns.mean()),
        "min_return_q05": float(np.quantile(min_returns, 0.05)),
        "min_return_q50": float(np.quantile(min_returns, 0.50)),
        "min_return_q95": float(np.quantile(min_returns, 0.95)),
        "note": "Historical sampled excursion statistics; not a live model prediction.",
    }


def build_first_passage_discovery(
    prices: pd.DataFrame,
    config: DiscoveryConfig = DiscoveryConfig(),
) -> dict[str, Any]:
    required = {"timestamp_ms", "price", "high", "low"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"discovery prices missing columns: {sorted(missing)}")
    ordered = prices.sort_values("timestamp_ms", ignore_index=True)
    ordered = ordered.drop_duplicates("timestamp_ms", keep="last")
    max_horizon = config.max_horizon_minutes
    if len(ordered) <= max_horizon + config.volatility_window_minutes:
        return {
            "status": "WAIT",
            "reason": "insufficient_history_for_max_discovery_horizon",
            "required_rows": max_horizon + config.volatility_window_minutes + 1,
            "available_rows": int(len(ordered)),
        }

    timestamps = ordered["timestamp_ms"].to_numpy(dtype=np.int64)
    closes = ordered["price"].to_numpy(dtype=np.float64)
    highs = ordered["high"].to_numpy(dtype=np.float64)
    lows = ordered["low"].to_numpy(dtype=np.float64)

    first_anchor = config.volatility_window_minutes
    final_anchor_exclusive = len(ordered) - max_horizon
    anchor_indices = np.arange(
        first_anchor,
        final_anchor_exclusive,
        config.anchor_stride_minutes,
        dtype=np.int64,
    )
    expected_end = timestamps[anchor_indices] + max_horizon * MINUTE_MS
    contiguous = timestamps[anchor_indices + max_horizon] == expected_end
    valid_indices = anchor_indices[contiguous]
    if valid_indices.size == 0:
        return {
            "status": "WAIT",
            "reason": "no_contiguous_discovery_paths",
            "sampled_anchors": int(anchor_indices.size),
        }

    upper_steps = np.zeros(valid_indices.size, dtype=np.int32)
    lower_steps = np.zeros(valid_indices.size, dtype=np.int32)
    excursion_max: dict[int, list[np.ndarray]] = {
        horizon: [] for horizon in config.horizons_minutes
    }
    excursion_min: dict[int, list[np.ndarray]] = {
        horizon: [] for horizon in config.horizons_minutes
    }

    high_windows = sliding_window_view(highs[1:], max_horizon)
    low_windows = sliding_window_view(lows[1:], max_horizon)
    upper_barriers = closes * (1.0 + config.threshold_return)
    lower_barriers = closes * (1.0 - config.threshold_return)

    for start in range(0, valid_indices.size, config.batch_rows):
        stop = min(start + config.batch_rows, valid_indices.size)
        indices = valid_indices[start:stop]
        chunk_highs = high_windows[indices]
        chunk_lows = low_windows[indices]
        upper_hits = chunk_highs >= upper_barriers[indices, None]
        lower_hits = chunk_lows <= lower_barriers[indices, None]
        upper_any = upper_hits.any(axis=1)
        lower_any = lower_hits.any(axis=1)
        upper_steps[start:stop] = np.where(
            upper_any,
            upper_hits.argmax(axis=1) + 1,
            0,
        )
        lower_steps[start:stop] = np.where(
            lower_any,
            lower_hits.argmax(axis=1) + 1,
            0,
        )
        anchor_prices = closes[indices]
        for horizon in config.horizons_minutes:
            horizon_high = np.max(chunk_highs[:, :horizon], axis=1)
            horizon_low = np.min(chunk_lows[:, :horizon], axis=1)
            excursion_max[horizon].append(horizon_high / anchor_prices - 1.0)
            excursion_min[horizon].append(horizon_low / anchor_prices - 1.0)

    upper_touch_timestamps = np.where(
        upper_steps > 0,
        timestamps[valid_indices] + upper_steps.astype(np.int64) * MINUTE_MS,
        0,
    )
    lower_touch_timestamps = np.where(
        lower_steps > 0,
        timestamps[valid_indices] + lower_steps.astype(np.int64) * MINUTE_MS,
        0,
    )
    up_first_full = (upper_steps > 0) & (
        (lower_steps == 0) | (upper_steps < lower_steps)
    )
    down_first_full = (lower_steps > 0) & (
        (upper_steps == 0) | (lower_steps < upper_steps)
    )
    ambiguous_full = (upper_steps > 0) & (lower_steps > 0) & (
        upper_steps == lower_steps
    )

    regime_labels, regime_thresholds = _regime_labels(
        closes,
        valid_indices,
        config.volatility_window_minutes,
    )
    horizons: dict[str, Any] = {}
    for horizon in config.horizons_minutes:
        upper_reached = (upper_steps > 0) & (upper_steps <= horizon)
        lower_reached = (lower_steps > 0) & (lower_steps <= horizon)
        up_first = upper_reached & (
            ~lower_reached | (upper_steps < lower_steps)
        )
        down_first = lower_reached & (
            ~upper_reached | (lower_steps < upper_steps)
        )
        ambiguous = upper_reached & lower_reached & (upper_steps == lower_steps)
        no_touch = ~upper_reached & ~lower_reached
        upper_times = upper_touch_timestamps[upper_reached]
        lower_times = lower_touch_timestamps[lower_reached]
        max_returns = np.concatenate(excursion_max[horizon])
        min_returns = np.concatenate(excursion_min[horizon])

        regime_rates: dict[str, Any] = {}
        for regime in ("LOW", "MEDIUM", "HIGH"):
            mask = regime_labels == regime
            count = int(mask.sum())
            any_touch = (upper_reached | lower_reached) & mask
            regime_rates[regime] = {
                "sample_rows": count,
                "any_10pct_touch_count": int(any_touch.sum()),
                "any_10pct_touch_rate": (
                    None if count == 0 else float(any_touch.sum() / count)
                ),
                "up_first_count": int((up_first & mask).sum()),
                "down_first_count": int((down_first & mask).sum()),
            }

        horizons[str(horizon)] = {
            "sample_rows": int(valid_indices.size),
            "upper_10_reached_count": int(upper_reached.sum()),
            "lower_10_reached_count": int(lower_reached.sum()),
            "upper_10_reached_rate": float(upper_reached.mean()),
            "lower_10_reached_rate": float(lower_reached.mean()),
            "up_first_count": int(up_first.sum()),
            "down_first_count": int(down_first.sum()),
            "ambiguous_same_minute_count": int(ambiguous.sum()),
            "no_touch_count": int(no_touch.sum()),
            "any_10pct_touch_rate": float((upper_reached | lower_reached).mean()),
            "upper_independent_clusters": _cluster_count(
                upper_times,
                config.independent_cluster_separation_minutes,
            ),
            "lower_independent_clusters": _cluster_count(
                lower_times,
                config.independent_cluster_separation_minutes,
            ),
            "empirical_excursion": _empirical_excursion_summary(
                max_returns,
                min_returns,
            ),
            "volatility_regime_rates": regime_rates,
        }

    up_first_steps = np.where(up_first_full, upper_steps, 0)
    down_first_steps = np.where(down_first_full, lower_steps, 0)
    report: dict[str, Any] = {
        "status": "READY",
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "generated_at_ms": int(time.time() * 1000),
        "source": "observed_completed_xrpusdt_one_minute_candles_only",
        "threshold_return": config.threshold_return,
        "anchor_stride_minutes": config.anchor_stride_minutes,
        "max_horizon_minutes": max_horizon,
        "sampled_anchor_count": int(anchor_indices.size),
        "valid_anchor_count": int(valid_indices.size),
        "excluded_non_contiguous_paths": int(anchor_indices.size - valid_indices.size),
        "data": {
            "price_rows": int(len(ordered)),
            "data_start_ms": int(timestamps[0]),
            "data_end_ms": int(timestamps[-1]),
            "latest_price": float(closes[-1]),
        },
        "return_distribution": _return_distribution_diagnostics(closes),
        "barrier_time_statistics": {
            "UP_10": _time_statistics(
                upper_steps,
                max_horizon_minutes=max_horizon,
                valid_anchor_count=int(valid_indices.size),
            ),
            "DOWN_10": _time_statistics(
                lower_steps,
                max_horizon_minutes=max_horizon,
                valid_anchor_count=int(valid_indices.size),
            ),
        },
        "first_touch_outcome_statistics": {
            "UP_10_FIRST": _time_statistics(
                up_first_steps,
                max_horizon_minutes=max_horizon,
                valid_anchor_count=int(valid_indices.size),
            ),
            "DOWN_10_FIRST": _time_statistics(
                down_first_steps,
                max_horizon_minutes=max_horizon,
                valid_anchor_count=int(valid_indices.size),
            ),
            "ambiguous_same_minute_count": int(ambiguous_full.sum()),
        },
        "volatility_regime_thresholds": regime_thresholds,
        "horizons": horizons,
        "methodology_notes": [
            "Anchors are sampled hourly to reduce overlap while touch times remain minute-precise.",
            "Conditional means describe observed hits only and are biased toward faster events.",
            "Restricted means censor non-hits at the fourteen-day maximum horizon.",
            "Outliers are retained because extreme moves are the research target.",
            "This report discovers candidate windows; it does not promote a trading model.",
        ],
        "external_context_feature_status": {
            "total_crypto_market_cap": "NOT_COLLECTED",
            "total_crypto_market_return": "NOT_COLLECTED",
            "xrp_market_cap": "NOT_COLLECTED",
            "xrp_market_cap_share": "NOT_COLLECTED",
            "xrp_global_volume_share": "NOT_COLLECTED",
            "xrp_turnover_volume_to_market_cap": "NOT_COLLECTED",
            "reason": (
                "Point-in-time market-wide and circulating-supply history must be collected "
                "before these features can enter leakage-safe training."
            ),
        },
    }
    return report


def generate_discovery_report(
    store: PartitionedPriceStore,
    output_path: Path,
    config: DiscoveryConfig = DiscoveryConfig(),
    *,
    force: bool = False,
    refresh_after_new_rows: int = 10_080,
) -> dict[str, Any]:
    if refresh_after_new_rows < 1:
        raise ValueError("refresh_after_new_rows must be positive")
    stats = store.stats()
    identity = _cache_identity(stats, config)
    if output_path.exists() and not force:
        cached_value = json.loads(output_path.read_text(encoding="utf-8"))
        if isinstance(cached_value, dict):
            cached = cast(dict[str, Any], cached_value)
            if _cached_report_is_fresh(
                cached,
                identity,
                refresh_after_new_rows=refresh_after_new_rows,
            ):
                return cached
    prices = store.load()
    report = build_first_passage_discovery(prices, config)
    report["cache_identity"] = asdict(identity)
    _atomic_write_json(report, output_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover empirical XRP ±10% first-passage windows"
    )
    parser.add_argument("--root", type=Path, default=Path("data/prices"))
    parser.add_argument("--legacy", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/first_passage_discovery.json"),
    )
    parser.add_argument("--anchor-stride-minutes", type=int, default=60)
    parser.add_argument("--batch-rows", type=int, default=128)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = DiscoveryConfig(
        anchor_stride_minutes=args.anchor_stride_minutes,
        batch_rows=args.batch_rows,
    )
    report = generate_discovery_report(
        PartitionedPriceStore(args.root, legacy_path=args.legacy),
        args.output,
        config,
        force=args.force,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_DISCOVERY_HORIZONS_MINUTES",
    "DISCOVERY_SCHEMA_VERSION",
    "DiscoveryConfig",
    "build_first_passage_discovery",
    "generate_discovery_report",
]
