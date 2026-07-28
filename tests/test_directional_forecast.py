from __future__ import annotations

import pytest

from xasp.directional_forecast import build_directional_forecast


def _row(
    horizon: int,
    up: float,
    down: float,
    no_event: float,
) -> dict[str, object]:
    return {
        "anchor_timestamp_ms": 1_000,
        "anchor_price": 1.25,
        "horizon_minutes": horizon,
        "p_up_02": up,
        "p_down_02": down,
        "p_no_event": no_event,
    }


def test_forecast_exposes_barrier_prices_even_while_waiting() -> None:
    payload = build_directional_forecast(
        first_touch_rows=[],
        envelope_rows=[],
        observed_price=1.25,
        observed_timestamp_ms=1_000,
    )

    assert payload["decision"] == "WAIT"
    assert payload["up_target_price"] == pytest.approx(1.275)
    assert payload["down_target_price"] == pytest.approx(1.225)
    assert payload["timeline"] == []


def test_forecast_returns_one_long_decision_and_hourly_timeline() -> None:
    touch = [
        _row(60, 0.10, 0.05, 0.85),
        _row(120, 0.20, 0.08, 0.72),
        _row(180, 0.35, 0.10, 0.55),
        _row(240, 0.48, 0.12, 0.40),
        _row(300, 0.55, 0.14, 0.31),
        _row(360, 0.58, 0.15, 0.27),
        _row(420, 0.60, 0.16, 0.24),
        _row(480, 0.62, 0.17, 0.21),
    ]
    envelope = [
        {
            "anchor_timestamp_ms": 1_000,
            "horizon_minutes": horizon,
            "max_price_q50": 1.25 + horizon / 100_000,
            "min_price_q50": 1.25 - horizon / 120_000,
        }
        for horizon in range(60, 481, 60)
    ]

    payload = build_directional_forecast(
        first_touch_rows=touch,
        envelope_rows=envelope,
        observed_price=1.26,
        observed_timestamp_ms=2_000,
    )

    assert payload["decision"] == "LONG"
    assert payload["directional_bias"] == "LONG"
    assert payload["predicted_target_price"] == pytest.approx(1.275)
    assert payload["directional_probability"] == pytest.approx(0.62 / 0.79)
    assert payload["event_probability"] == pytest.approx(0.79)
    assert payload["expected_touch_horizon_minutes"] == 180
    assert len(payload["timeline"]) == 8
    assert payload["timeline"][-1]["predicted_high_price_q50"] is not None


def test_forecast_waits_when_directional_edge_is_too_small() -> None:
    payload = build_directional_forecast(
        first_touch_rows=[_row(480, 0.31, 0.29, 0.40)],
        envelope_rows=[],
        observed_price=1.25,
        observed_timestamp_ms=1_000,
    )

    assert payload["directional_bias"] == "LONG"
    assert payload["decision"] == "WAIT"
    assert payload["decision_reason"] == "directional_edge_too_small"
