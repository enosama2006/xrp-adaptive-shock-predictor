from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from xasp.future_envelope import (
    EnvelopeConfig,
    build_future_envelope_targets,
    predict_envelope,
    train_future_envelope,
)


def _prices(rows: int = 181) -> pd.DataFrame:
    timestamps = np.arange(rows, dtype=np.int64) * 60_000
    price = 1.0 + np.sin(np.arange(rows) / 9.0) * 0.03 + np.arange(rows) * 0.0002
    return pd.DataFrame({"timestamp_ms": timestamps, "price": price})


def test_targets_capture_intrahorizon_close_extremes_when_ohlc_absent() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": np.arange(16, dtype=np.int64) * 60_000,
            "price": [100, 101, 102, 111, 108, 105, 99, 94, 96, 98, 100, 103, 104, 102, 101, 100],
        }
    )
    targets = build_future_envelope_targets(prices, horizons=(15,))
    row = targets.iloc[0]
    assert np.isclose(row["future_max_return"], 0.11)
    assert np.isclose(row["future_min_return"], -0.06)
    assert np.isclose(row["future_close_return"], 0.0)
    assert int(row["minutes_to_max"]) == 3
    assert int(row["minutes_to_min"]) == 7
    assert bool(row["hit_up_02"])
    assert bool(row["hit_down_02"])


def test_targets_use_intraminute_high_and_low_not_close_only() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": np.arange(16, dtype=np.int64) * 60_000,
            "price": [100.0] * 16,
            "high": [100.0, 112.0, *([100.0] * 14)],
            "low": [100.0, *([100.0] * 5), 89.0, *([100.0] * 9)],
        }
    )
    row = build_future_envelope_targets(prices, horizons=(15,)).iloc[0]
    assert np.isclose(row["future_max_return"], 0.12)
    assert np.isclose(row["future_min_return"], -0.11)
    assert int(row["minutes_to_max"]) == 1
    assert int(row["minutes_to_min"]) == 6
    assert bool(row["hit_up_02"])
    assert bool(row["hit_down_02"])


def test_incomplete_horizon_is_not_fabricated() -> None:
    targets = build_future_envelope_targets(_prices(10), horizons=(15,))
    assert targets.empty


def test_training_waits_without_real_sample_size() -> None:
    prices = _prices(100)
    targets = build_future_envelope_targets(prices, horizons=(15,))
    targets["feature"] = np.arange(len(targets), dtype=float)
    models, report = train_future_envelope(
        targets,
        ["feature"],
        15,
        EnvelopeConfig(minimum_rows=500, minimum_interval_samples=20),
    )
    assert models is None
    assert report.status == "WAIT"
    assert report.reason == "insufficient_real_rows"


class _FixedQuantile:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, _: pd.DataFrame) -> np.ndarray:
        return np.asarray([self.value])


def test_prediction_orders_and_conformalizes_hourly_close_path() -> None:
    models: dict[str, object] = {}
    for target in (
        "future_max_return",
        "future_min_return",
        "future_close_return",
    ):
        models[f"{target}_q05"] = _FixedQuantile(0.02)
        models[f"{target}_q50"] = _FixedQuantile(0.00)
        models[f"{target}_q95"] = _FixedQuantile(0.01)
        models[f"{target}_interval_expansion"] = 0.005

    prediction = predict_envelope(models, pd.DataFrame([{"feature": 1.0}]))

    assert prediction["future_close_return_q05"] == pytest.approx(-0.005)
    assert prediction["future_close_return_q50"] == pytest.approx(0.01)
    assert prediction["future_close_return_q95"] == pytest.approx(0.025)
