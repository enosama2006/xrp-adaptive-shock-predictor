from __future__ import annotations

import pandas as pd

from xasp.production_report import build_production_report


def test_production_report_uses_only_matured_observed_predictions() -> None:
    ledger = pd.DataFrame(
        [
            {
                "status": "FINAL",
                "actual_label": "UP_10",
                "horizon_minutes": 15,
                "p_up_10": 0.90,
                "p_down_10": 0.05,
                "p_no_event": 0.05,
            },
            {
                "status": "PENDING",
                "actual_label": None,
                "horizon_minutes": 15,
                "p_up_10": 0.99,
                "p_down_10": 0.005,
                "p_no_event": 0.005,
            },
        ]
    )
    prices = pd.DataFrame(
        {
            "timestamp_ms": [0, 60_000, 120_000],
            "price": [100.0, 105.0, 100.0],
            "high": [100.0, 111.0, 102.0],
            "low": [100.0, 99.0, 98.0],
        }
    )
    envelope = pd.DataFrame(
        [
            {
                "anchor_timestamp_ms": 0,
                "anchor_price": 100.0,
                "horizon_minutes": 2,
                "max_return_q05": 0.05,
                "max_return_q50": 0.10,
                "max_return_q95": 0.12,
                "min_return_q05": -0.03,
                "min_return_q50": -0.02,
                "min_return_q95": 0.00,
            }
        ]
    )
    report = build_production_report(
        ledger=ledger,
        envelope_predictions=envelope,
        prices=prices,
        runtime_status={"state": "RESEARCH_ONLY", "reason": "ok"},
    )
    assert report["first_touch"]["evaluated_rows"] == 1
    assert report["first_touch"]["high_confidence_accuracy"] == 1.0
    assert report["future_envelope"]["evaluated_rows"] == 1
    assert report["future_envelope"]["max_interval_coverage"] == 1.0
    assert report["future_envelope"]["min_interval_coverage"] == 1.0
