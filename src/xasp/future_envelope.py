"""Parallel future-envelope models trained only on observed market outcomes.

The first-touch classifier answers which barrier is reached first.  This module
answers a different question: what maximum and minimum return were observed
inside each future horizon, and what probabilistic envelope can be learned from
features available at the anchor time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline

HORIZONS = (15, 30, 45, 60)
QUANTILES = (0.05, 0.50, 0.95)


@dataclass(frozen=True, slots=True)
class EnvelopeConfig:
    minimum_rows: int = 2_000
    train_fraction: float = 0.70
    validation_fraction: float = 0.15
    required_interval_coverage: float = 0.85
    minimum_interval_samples: int = 200
    random_state: int = 17


@dataclass(frozen=True, slots=True)
class EnvelopeReport:
    status: str
    reason: str
    rows: int
    train_rows: int
    validation_rows: int
    test_rows: int
    horizon_minutes: int
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_future_envelope_targets(
    prices: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    """Create observed future max/min returns and their occurrence times.

    Input must be one observed price per minute.  A target is FINAL only when
    the complete future horizon is present.  No interpolation or synthetic row
    is introduced.
    """

    required = {"timestamp_ms", "price"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price dataset missing columns: {sorted(missing)}")
    frame = prices[["timestamp_ms", "price"]].drop_duplicates("timestamp_ms", keep="last")
    frame = frame.sort_values("timestamp_ms", ignore_index=True)
    if frame.empty:
        return pd.DataFrame()
    if (frame["price"] <= 0).any():
        raise ValueError("prices must be positive")

    timestamps = frame["timestamp_ms"].to_numpy(dtype=np.int64)
    values = frame["price"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    index_by_timestamp = {int(ts): idx for idx, ts in enumerate(timestamps)}

    for anchor_index, (anchor_ms, anchor_price) in enumerate(zip(timestamps, values, strict=True)):
        for horizon in horizons:
            end_ms = int(anchor_ms) + horizon * 60_000
            end_index = index_by_timestamp.get(end_ms)
            if end_index is None or end_index <= anchor_index:
                continue
            segment = values[anchor_index + 1 : end_index + 1]
            if len(segment) != horizon:
                continue
            max_offset = int(np.argmax(segment)) + 1
            min_offset = int(np.argmin(segment)) + 1
            max_price = float(segment[max_offset - 1])
            min_price = float(segment[min_offset - 1])
            rows.append(
                {
                    "anchor_timestamp_ms": int(anchor_ms),
                    "anchor_price": float(anchor_price),
                    "horizon_minutes": horizon,
                    "horizon_end_ms": end_ms,
                    "future_max_price": max_price,
                    "future_min_price": min_price,
                    "future_max_return": max_price / float(anchor_price) - 1.0,
                    "future_min_return": min_price / float(anchor_price) - 1.0,
                    "minutes_to_max": max_offset,
                    "minutes_to_min": min_offset,
                    "hit_up_02": max_price >= float(anchor_price) * 1.02,
                    "hit_up_05": max_price >= float(anchor_price) * 1.05,
                    "hit_up_10": max_price >= float(anchor_price) * 1.10,
                    "hit_down_02": min_price <= float(anchor_price) * 0.98,
                    "hit_down_05": min_price <= float(anchor_price) * 0.95,
                    "hit_down_10": min_price <= float(anchor_price) * 0.90,
                    "status": "FINAL",
                }
            )
    return pd.DataFrame(rows)


def _split(frame: pd.DataFrame, config: EnvelopeConfig) -> tuple[pd.DataFrame, ...]:
    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True)
    train_end = int(len(ordered) * config.train_fraction)
    validation_end = int(len(ordered) * (config.train_fraction + config.validation_fraction))
    return ordered.iloc[:train_end], ordered.iloc[train_end:validation_end], ordered.iloc[validation_end:]


def _quantile_pipeline(quantile: float, config: EnvelopeConfig) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            (
                "model",
                HistGradientBoostingRegressor(
                    loss="quantile",
                    quantile=quantile,
                    max_iter=300,
                    learning_rate=0.05,
                    max_leaf_nodes=31,
                    l2_regularization=1.0,
                    random_state=config.random_state,
                ),
            ),
        ]
    )


def train_future_envelope(
    dataset: pd.DataFrame,
    feature_names: list[str],
    horizon_minutes: int,
    config: EnvelopeConfig = EnvelopeConfig(),
) -> tuple[dict[str, Pipeline] | None, EnvelopeReport]:
    """Fit quantile models for future maximum and minimum return.

    The model is accepted for research output only when the untouched test set
    shows at least the configured empirical coverage for the 5%-95% interval.
    This is an evidence gate, not a promise that future forecasts will be right.
    """

    required = {
        "anchor_timestamp_ms",
        "horizon_minutes",
        "future_max_return",
        "future_min_return",
        *feature_names,
    }
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"envelope dataset missing columns: {sorted(missing)}")
    usable = dataset[dataset["horizon_minutes"] == horizon_minutes].dropna(
        subset=["future_max_return", "future_min_return"]
    )
    if len(usable) < config.minimum_rows:
        return None, EnvelopeReport(
            "WAIT",
            "insufficient_real_rows",
            len(usable),
            0,
            0,
            0,
            horizon_minutes,
            {},
        )
    train, validation, test = _split(usable, config)
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("temporal split produced an empty partition")

    models: dict[str, Pipeline] = {}
    metrics: dict[str, Any] = {}
    all_covered = True
    for target in ("future_max_return", "future_min_return"):
        target_models: dict[float, Pipeline] = {}
        for quantile in QUANTILES:
            model = _quantile_pipeline(quantile, config)
            model.fit(train[feature_names], train[target])
            target_models[quantile] = model
            models[f"{target}_q{int(quantile * 100):02d}"] = model

        lower = target_models[0.05].predict(test[feature_names])
        median = target_models[0.50].predict(test[feature_names])
        upper = target_models[0.95].predict(test[feature_names])
        truth = test[target].to_numpy(dtype=float)
        interval_coverage = float(np.mean((truth >= lower) & (truth <= upper)))
        ordered_fraction = float(np.mean((lower <= median) & (median <= upper)))
        metrics[target] = {
            "mae_median": float(mean_absolute_error(truth, median)),
            "interval_coverage_90": interval_coverage,
            "interval_mean_width": float(np.mean(upper - lower)),
            "quantile_order_fraction": ordered_fraction,
            "test_start_ms": int(test["anchor_timestamp_ms"].min()),
            "test_end_ms": int(test["anchor_timestamp_ms"].max()),
        }
        all_covered = all_covered and (
            len(test) >= config.minimum_interval_samples
            and interval_coverage >= config.required_interval_coverage
            and ordered_fraction >= 0.99
        )

    status = "RESEARCH_ONLY" if all_covered else "WAIT"
    reason = "empirical_interval_gate_passed_not_trading_promoted" if all_covered else "coverage_below_required_85pct"
    report = EnvelopeReport(
        status,
        reason,
        len(usable),
        len(train),
        len(validation),
        len(test),
        horizon_minutes,
        metrics,
    )
    return (models if all_covered else None), report


def predict_envelope(models: dict[str, Pipeline], row: pd.DataFrame) -> dict[str, float]:
    output: dict[str, float] = {}
    for name, model in models.items():
        output[name] = float(model.predict(row)[0])
    return output
