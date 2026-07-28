"""Hourly close-path and future-excursion models from observed outcomes.

Model B answers which ±2% barrier is reached first. Model A estimates observed
closing-price quantiles at each hourly boundary plus upside/downside excursions
inside each horizon using candle closes/highs/lows.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_pinball_loss
from sklearn.pipeline import Pipeline

from .horizons import RESEARCH_HORIZONS_MINUTES
from .target_definition import TARGET_DEFINITION_VERSION, TARGET_DOWN_RETURN, TARGET_UP_RETURN

HORIZONS = RESEARCH_HORIZONS_MINUTES
QUANTILES = (0.05, 0.50, 0.95)


@dataclass(frozen=True, slots=True)
class EnvelopeConfig:
    minimum_rows: int = 2_000
    train_fraction: float = 0.70
    validation_fraction: float = 0.15
    required_interval_coverage: float = 0.85
    minimum_interval_samples: int = 200
    random_state: int = 17
    embargo_ms: int | None = None

    def __post_init__(self) -> None:
        if self.minimum_rows < 30:
            raise ValueError("minimum_rows is too small")
        if not 0.5 <= self.train_fraction < 0.9:
            raise ValueError("train_fraction must be in [0.5, 0.9)")
        if not 0.05 <= self.validation_fraction < 0.3:
            raise ValueError("validation_fraction must be in [0.05, 0.3)")
        if self.train_fraction + self.validation_fraction >= 0.95:
            raise ValueError("at least 5% must remain for untouched test")
        if not 0.5 <= self.required_interval_coverage <= 1.0:
            raise ValueError("required_interval_coverage must be in [0.5, 1]")
        if self.minimum_interval_samples < 1:
            raise ValueError("minimum_interval_samples must be positive")
        if self.embargo_ms is not None and self.embargo_ms < 0:
            raise ValueError("embargo_ms must be non-negative")


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
    """Create observed close/max/min returns and excursion occurrence times.

    A target is emitted only when every expected minute exists. No interpolation
    or synthetic row is introduced.
    """

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
    if (frame["price"] <= 0).any():
        raise ValueError("prices must be positive")

    frame["high"] = frame["high"] if "high" in frame else frame["price"]
    frame["low"] = frame["low"] if "low" in frame else frame["price"]
    if (frame["high"] < frame["low"]).any():
        raise ValueError("candle high must be greater than or equal to low")
    if ((frame["price"] > frame["high"]) | (frame["price"] < frame["low"])).any():
        raise ValueError("close price must lie inside candle high/low")

    timestamps = frame["timestamp_ms"].to_numpy(dtype=np.int64)
    closes = frame["price"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    index_by_timestamp = {int(ts): idx for idx, ts in enumerate(timestamps)}

    for anchor_index, (anchor_ms, anchor_price) in enumerate(
        zip(timestamps, closes, strict=True)
    ):
        for horizon in horizons:
            end_ms = int(anchor_ms) + horizon * 60_000
            end_index = index_by_timestamp.get(end_ms)
            if end_index is None or end_index <= anchor_index:
                continue
            expected = np.arange(
                int(anchor_ms) + 60_000,
                end_ms + 1,
                60_000,
                dtype=np.int64,
            )
            actual = timestamps[anchor_index + 1 : end_index + 1]
            if len(actual) != horizon or not np.array_equal(actual, expected):
                continue
            high_segment = highs[anchor_index + 1 : end_index + 1]
            low_segment = lows[anchor_index + 1 : end_index + 1]
            max_offset = int(np.argmax(high_segment)) + 1
            min_offset = int(np.argmin(low_segment)) + 1
            max_price = float(high_segment[max_offset - 1])
            min_price = float(low_segment[min_offset - 1])
            close_price = float(closes[end_index])
            rows.append(
                {
                    "anchor_timestamp_ms": int(anchor_ms),
                    "anchor_price": float(anchor_price),
                    "horizon_minutes": horizon,
                    "horizon_end_ms": end_ms,
                    "future_max_price": max_price,
                    "future_min_price": min_price,
                    "future_close_price": close_price,
                    "future_max_return": max_price / float(anchor_price) - 1.0,
                    "future_min_return": min_price / float(anchor_price) - 1.0,
                    "future_close_return": close_price / float(anchor_price) - 1.0,
                    "minutes_to_max": max_offset,
                    "minutes_to_min": min_offset,
                    "hit_up_02": max_price
                    >= float(anchor_price) * (1.0 + TARGET_UP_RETURN),
                    "hit_down_02": min_price
                    <= float(anchor_price) * (1.0 + TARGET_DOWN_RETURN),
                    "status": "FINAL",
                }
            )
    return pd.DataFrame(rows)


def _purged_split(
    frame: pd.DataFrame,
    config: EnvelopeConfig,
    horizon_minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True)
    n_rows = len(ordered)
    train_cut = int(n_rows * config.train_fraction)
    validation_cut = int(n_rows * (config.train_fraction + config.validation_fraction))
    if train_cut <= 0 or validation_cut <= train_cut or validation_cut >= n_rows:
        raise ValueError("temporal split produced an empty raw partition")

    validation_boundary = int(ordered.iloc[train_cut]["anchor_timestamp_ms"])
    test_boundary = int(ordered.iloc[validation_cut]["anchor_timestamp_ms"])
    horizon_ms = horizon_minutes * 60_000
    embargo_ms = horizon_ms if config.embargo_ms is None else config.embargo_ms

    raw_train = ordered.iloc[:train_cut]
    raw_validation = ordered.iloc[train_cut:validation_cut]
    raw_test = ordered.iloc[validation_cut:]

    train = raw_train[raw_train["horizon_end_ms"] <= validation_boundary].copy()
    validation = raw_validation[
        (raw_validation["anchor_timestamp_ms"] >= validation_boundary + embargo_ms)
        & (raw_validation["horizon_end_ms"] <= test_boundary)
    ].copy()
    test = raw_test[
        raw_test["anchor_timestamp_ms"] >= test_boundary + embargo_ms
    ].copy()

    audit = {
        "raw_train_rows": int(len(raw_train)),
        "raw_validation_rows": int(len(raw_validation)),
        "raw_test_rows": int(len(raw_test)),
        "purged_train_rows": int(len(raw_train) - len(train)),
        "purged_or_embargoed_validation_rows": int(len(raw_validation) - len(validation)),
        "embargoed_test_rows": int(len(raw_test) - len(test)),
        "validation_boundary_ms": validation_boundary,
        "test_boundary_ms": test_boundary,
        "horizon_ms": horizon_ms,
        "embargo_ms": embargo_ms,
    }
    return train, validation, test, audit


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


def _target_metrics(
    target_models: dict[float, Pipeline],
    frame: pd.DataFrame,
    feature_names: list[str],
    target: str,
    *,
    interval_expansion: float = 0.0,
) -> dict[str, float | int]:
    raw = np.column_stack(
        [
            target_models[0.05].predict(frame[feature_names]),
            target_models[0.50].predict(frame[feature_names]),
            target_models[0.95].predict(frame[feature_names]),
        ]
    )
    raw_ordered = (raw[:, 0] <= raw[:, 1]) & (raw[:, 1] <= raw[:, 2])
    ordered = np.sort(raw, axis=1)
    lower = ordered[:, 0] - interval_expansion
    median = ordered[:, 1]
    upper = ordered[:, 2] + interval_expansion
    truth = frame[target].to_numpy(dtype=float)
    return {
        "rows": int(len(frame)),
        "mae_median": float(mean_absolute_error(truth, median)),
        "pinball_q05": float(mean_pinball_loss(truth, lower, alpha=0.05)),
        "pinball_q50": float(mean_pinball_loss(truth, median, alpha=0.50)),
        "pinball_q95": float(mean_pinball_loss(truth, upper, alpha=0.95)),
        "interval_coverage_90": float(np.mean((truth >= lower) & (truth <= upper))),
        "interval_mean_width": float(np.mean(upper - lower)),
        "raw_quantile_order_fraction": float(np.mean(raw_ordered)),
        "quantile_order_fraction": float(
            np.mean((lower <= median) & (median <= upper))
        ),
        "conformal_interval_expansion": float(interval_expansion),
        "start_ms": int(frame["anchor_timestamp_ms"].min()),
        "end_ms": int(frame["anchor_timestamp_ms"].max()),
    }


def train_future_envelope(
    dataset: pd.DataFrame,
    feature_names: list[str],
    horizon_minutes: int,
    config: EnvelopeConfig = EnvelopeConfig(),
) -> tuple[dict[str, Any] | None, EnvelopeReport]:
    """Fit purged quantile models for hourly close, maximum, and minimum return."""

    required = {
        "anchor_timestamp_ms",
        "horizon_minutes",
        "horizon_end_ms",
        "future_max_return",
        "future_min_return",
        "future_close_return",
        *feature_names,
    }
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"envelope dataset missing columns: {sorted(missing)}")
    usable = dataset[dataset["horizon_minutes"] == horizon_minutes].dropna(
        subset=[
            "future_max_return",
            "future_min_return",
            "future_close_return",
        ]
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

    train, validation, test, split_audit = _purged_split(
        usable,
        config,
        horizon_minutes,
    )
    if min(len(train), len(validation), len(test)) == 0:
        return None, EnvelopeReport(
            "WAIT",
            "insufficient_rows_after_purge_and_embargo",
            len(usable),
            len(train),
            len(validation),
            len(test),
            horizon_minutes,
            {"split_audit": split_audit},
        )

    models: dict[str, Any] = {}
    metrics: dict[str, Any] = {
        "split_audit": split_audit,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "target_up_return": TARGET_UP_RETURN,
        "target_down_return": TARGET_DOWN_RETURN,
        "target_up_observed_rate": float(
            usable.get(
                "hit_up_02",
                usable["future_max_return"] >= TARGET_UP_RETURN,
            ).mean()
        ),
        "target_down_observed_rate": float(
            usable.get(
                "hit_down_02",
                usable["future_min_return"] <= TARGET_DOWN_RETURN,
            ).mean()
        ),
    }
    all_covered = True
    for target in (
        "future_max_return",
        "future_min_return",
        "future_close_return",
    ):
        target_models: dict[float, Pipeline] = {}
        for quantile in QUANTILES:
            model = _quantile_pipeline(quantile, config)
            model.fit(train[feature_names], train[target])
            target_models[quantile] = model
            models[f"{target}_q{int(quantile * 100):02d}"] = model

        validation_raw = np.column_stack(
            [
                target_models[0.05].predict(validation[feature_names]),
                target_models[0.50].predict(validation[feature_names]),
                target_models[0.95].predict(validation[feature_names]),
            ]
        )
        validation_ordered = np.sort(validation_raw, axis=1)
        validation_truth = validation[target].to_numpy(dtype=float)
        nonconformity = np.maximum(
            validation_ordered[:, 0] - validation_truth,
            validation_truth - validation_ordered[:, 2],
        )
        nonconformity = np.maximum(nonconformity, 0.0)
        finite_sample_quantile = min(
            1.0,
            math.ceil((len(nonconformity) + 1) * 0.90) / len(nonconformity),
        )
        interval_expansion = float(
            np.quantile(
                nonconformity,
                finite_sample_quantile,
                method="higher",
            )
        )
        models[f"{target}_interval_expansion"] = interval_expansion
        validation_metrics = _target_metrics(
            target_models,
            validation,
            feature_names,
            target,
            interval_expansion=interval_expansion,
        )
        test_metrics = _target_metrics(
            target_models,
            test,
            feature_names,
            target,
            interval_expansion=interval_expansion,
        )
        metrics[target] = {
            "validation": validation_metrics,
            "test": test_metrics,
        }
        all_covered = all_covered and (
            len(test) >= config.minimum_interval_samples
            and float(test_metrics["interval_coverage_90"])
            >= config.required_interval_coverage
            and float(test_metrics["quantile_order_fraction"]) >= 0.99
        )

    status = "RESEARCH_ONLY" if all_covered else "WAIT"
    reason = (
        "empirical_interval_gate_passed_not_trading_promoted"
        if all_covered
        else "coverage_below_required_85pct"
    )
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


def predict_envelope(models: dict[str, Any], row: pd.DataFrame) -> dict[str, float]:
    output: dict[str, float] = {}
    for target in (
        "future_max_return",
        "future_min_return",
        "future_close_return",
    ):
        raw = sorted(
            float(models[f"{target}_q{quantile:02d}"].predict(row)[0])
            for quantile in (5, 50, 95)
        )
        expansion = float(models.get(f"{target}_interval_expansion", 0.0))
        output[f"{target}_q05"] = raw[0] - expansion
        output[f"{target}_q50"] = raw[1]
        output[f"{target}_q95"] = raw[2] + expansion
    return output
