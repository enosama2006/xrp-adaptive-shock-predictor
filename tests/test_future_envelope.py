from __future__ import annotations

import numpy as np
import pandas as pd

from xasp.future_envelope import EnvelopeConfig, build_future_envelope_targets, train_future_envelope


def _prices(rows: int = 181) -> pd.DataFrame:
    timestamps = np.arange(rows, dtype=np.int64) * 60_000
    price = 1.0 + np.sin(np.arange(rows) / 9.0) * 0.03 + np.arange(rows) * 0.0002
    return pd.DataFrame({"timestamp_ms": timestamps, "price": price})


def test_targets_capture_intrahorizon_high_and_low_not_only_endpoint() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": np.arange(16, dtype=np.int64) * 60_000,
            "price": [100, 101, 102, 111, 108, 105, 99, 94, 96, 98, 100, 103, 104, 102, 101, 100],
        }
    )
    targets = build_future_envelope_targets(prices, horizons=(15,))
    row = targets.iloc[0]
    assert row["future_max_return"] == 0.11
    assert row["future_min_return"] == -0.06
    assert int(row["minutes_to_max"]) == 3
    assert int(row["minutes_to_min"]) == 7
    assert bool(row["hit_up_10"])
    assert not bool(row["hit_down_10"])


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
