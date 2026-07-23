from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from xasp.extended_runtime import _anchor_horizon_matrix_complete
from xasp.fast_future_envelope import build_future_envelope_targets_fast
from xasp.first_touch_v4 import FIRST_TOUCH_GATE_VERSION, train_first_touch_v4
from xasp.horizons import RESEARCH_HORIZONS_MINUTES, RESEARCH_HORIZON_SET_VERSION
from xasp.production_report_v2 import build_production_report
from xasp.baseline import BaselineConfig

MINUTE = 60_000


def test_governed_horizon_set_extends_to_eight_hours() -> None:
    assert RESEARCH_HORIZONS_MINUTES == (15, 30, 45, 60, 120, 180, 240, 480)
    assert RESEARCH_HORIZON_SET_VERSION.endswith("120-180-240-480-v1")


def test_anchor_horizon_matrix_requires_every_horizon_for_every_anchor() -> None:
    rows = [
        {"anchor_timestamp_ms": anchor, "horizon_minutes": horizon}
        for anchor in (0, MINUTE)
        for horizon in RESEARCH_HORIZONS_MINUTES
    ]
    complete = pd.DataFrame(rows)
    incomplete = complete.drop(complete.index[-1]).reset_index(drop=True)

    assert _anchor_horizon_matrix_complete(complete) is True
    assert _anchor_horizon_matrix_complete(incomplete) is False


def test_fast_model_a_targets_include_full_eight_hour_window() -> None:
    rows = 481
    timestamps = np.arange(rows, dtype=np.int64) * MINUTE
    prices = pd.DataFrame(
        {
            "timestamp_ms": timestamps,
            "price": np.linspace(1.0, 1.1, rows),
            "high": np.linspace(1.0, 1.1, rows) + 0.001,
            "low": np.linspace(1.0, 1.1, rows) - 0.001,
        }
    )

    targets = build_future_envelope_targets_fast(
        prices,
        horizons=(480,),
        chunk_rows=10,
    )

    assert len(targets) == 1
    row = targets.iloc[0]
    assert int(row["horizon_minutes"]) == 480
    assert int(row["horizon_end_ms"]) == 480 * MINUTE
    assert int(row["minutes_to_max"]) == 480


def test_first_touch_v4_fails_closed_without_independent_late_events() -> None:
    rows = 600
    frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": np.arange(rows, dtype=np.int64) * MINUTE,
            "status": ["FINAL"] * rows,
            "label": ["UP_10"] * 5 + ["DOWN_10"] * 5 + ["NO_EVENT"] * (rows - 10),
            "touch_timestamp_ms": [
                *(index * MINUTE for index in range(10)),
                *([None] * (rows - 10)),
            ],
            "feature": [1.0] * 5 + [-1.0] * 5 + [0.0] * (rows - 10),
        }
    )

    model, report = train_first_touch_v4(
        frame,
        ["feature"],
        BaselineConfig(
            minimum_rows=500,
            label_horizon_ms=15 * MINUTE,
            embargo_ms=15 * MINUTE,
        ),
    )

    assert model is None
    assert report.status == "WAIT"
    assert report.metrics["gate_methodology_version"] == FIRST_TOUCH_GATE_VERSION
    assert report.reason == (
        "insufficient_independent_directional_events_across_untouched_periods"
    )


def test_production_report_declares_all_independent_horizons() -> None:
    report = build_production_report(
        ledger=pd.DataFrame(),
        envelope_predictions=pd.DataFrame(),
        prices=pd.DataFrame(),
        runtime_status={"state": "WAIT", "reason": "test"},
    )

    assert report["configured_horizons_minutes"] == list(RESEARCH_HORIZONS_MINUTES)
    assert set(report["future_envelope"]["per_horizon"]) == {
        str(value) for value in RESEARCH_HORIZONS_MINUTES
    }
    assert report["trading_readiness"] == "WAIT"


def test_browser_ui_contains_eight_hour_horizon() -> None:
    javascript = Path("app.js").read_text(encoding="utf-8")
    assert "const HORIZONS = [15, 30, 45, 60, 120, 180, 240, 480];" in javascript
    assert "480 دقيقة (8 ساعات)" in javascript
