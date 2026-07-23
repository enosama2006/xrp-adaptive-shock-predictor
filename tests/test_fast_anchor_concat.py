from __future__ import annotations

import warnings

import pandas as pd

from xasp.anchor_dataset import ANCHOR_COLUMNS
from xasp.fast_anchor_dataset import _append_anchor_rows_without_all_na_concat_warning


def _row(timestamp_ms: int, *, pending: bool) -> dict[str, object]:
    return {
        "anchor_timestamp_ms": timestamp_ms,
        "anchor_price": 1.0,
        "horizon_minutes": 15,
        "horizon_end_ms": timestamp_ms + 15 * 60_000,
        "upper_barrier_price": 1.1,
        "lower_barrier_price": 0.9,
        "max_price": None if pending else 1.01,
        "min_price": None if pending else 0.99,
        "max_return": None if pending else 0.01,
        "min_return": None if pending else -0.01,
        "label": "INCOMPLETE" if pending else "NO_EVENT",
        "touch_timestamp_ms": None,
        "touch_price": None,
        "status": "PENDING" if pending else "FINAL",
        "reason": "horizon_not_mature" if pending else "no_barrier_touched_within_horizon",
    }


def test_live_tail_append_avoids_all_na_concat_future_warning() -> None:
    existing = pd.DataFrame([_row(0, pending=False)], columns=ANCHOR_COLUMNS)
    incoming = [_row(60_000, pending=True)]

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        combined = _append_anchor_rows_without_all_na_concat_warning(existing, incoming)

    assert list(combined.columns) == ANCHOR_COLUMNS
    assert len(combined) == 2
    assert combined.iloc[-1]["status"] == "PENDING"
    assert pd.isna(combined.iloc[-1]["touch_timestamp_ms"])
    assert pd.isna(combined.iloc[-1]["touch_price"])
