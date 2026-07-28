"""Human-readable cumulative ±2% directional forecast.

The trained first-touch models remain the source of probability estimates.
This module turns their hourly outputs into one auditable LONG/SHORT/WAIT
summary without inventing a price target: the predicted target is one of the
two governed barriers around the observed anchor price.
"""

from __future__ import annotations

import math
from typing import Any

from .horizons import RESEARCH_HORIZONS_MINUTES
from .target_definition import TARGET_DOWN_RETURN, TARGET_UP_RETURN

MIN_EVENT_PROBABILITY = 0.20
MIN_CONDITIONAL_DIRECTION_CONFIDENCE = 0.55


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _probability(value: Any) -> float:
    parsed = _finite_float(value)
    if parsed is None:
        return 0.0
    return min(1.0, max(0.0, parsed))


def _latest_anchor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = [
        row
        for row in rows
        if int(row.get("horizon_minutes", 0)) in RESEARCH_HORIZONS_MINUTES
        and row.get("anchor_timestamp_ms") is not None
    ]
    if not usable:
        return []
    latest_anchor = max(int(row["anchor_timestamp_ms"]) for row in usable)
    return sorted(
        (
            row
            for row in usable
            if int(row["anchor_timestamp_ms"]) == latest_anchor
        ),
        key=lambda row: int(row["horizon_minutes"]),
    )


def _envelope_by_horizon(
    rows: list[dict[str, Any]],
    anchor_timestamp_ms: int | None,
) -> dict[int, dict[str, Any]]:
    if anchor_timestamp_ms is None:
        return {}
    return {
        int(row["horizon_minutes"]): row
        for row in rows
        if row.get("anchor_timestamp_ms") == anchor_timestamp_ms
        and int(row.get("horizon_minutes", 0)) in RESEARCH_HORIZONS_MINUTES
    }


def _arrival_window(
    timeline: list[dict[str, Any]],
    probability_key: str,
) -> int | None:
    """Return the hourly bin with the largest newly accumulated probability."""

    previous = 0.0
    best_horizon: int | None = None
    best_increment = -1.0
    cumulative = 0.0
    for row in timeline:
        cumulative = max(cumulative, float(row[probability_key]))
        increment = max(0.0, cumulative - previous)
        if increment > best_increment:
            best_increment = increment
            best_horizon = int(row["horizon_minutes"])
        previous = cumulative
    return best_horizon


def build_directional_forecast(
    *,
    first_touch_rows: list[dict[str, Any]],
    envelope_rows: list[dict[str, Any]],
    observed_price: float | None,
    observed_timestamp_ms: int | None,
) -> dict[str, Any]:
    """Build one directional decision and an hourly cumulative forecast table."""

    touch = _latest_anchor_rows(first_touch_rows)
    anchor_price = observed_price
    anchor_timestamp_ms = observed_timestamp_ms
    if touch:
        anchor_price = _finite_float(touch[-1].get("anchor_price")) or anchor_price
        anchor_timestamp_ms = int(touch[-1]["anchor_timestamp_ms"])

    upper_price = (
        None
        if anchor_price is None
        else float(anchor_price) * (1.0 + TARGET_UP_RETURN)
    )
    lower_price = (
        None
        if anchor_price is None
        else float(anchor_price) * (1.0 + TARGET_DOWN_RETURN)
    )
    base: dict[str, Any] = {
        "anchor_timestamp_ms": anchor_timestamp_ms,
        "anchor_price": anchor_price,
        "up_target_return": TARGET_UP_RETURN,
        "down_target_return": TARGET_DOWN_RETURN,
        "up_target_price": upper_price,
        "down_target_price": lower_price,
        "configured_horizons_minutes": list(RESEARCH_HORIZONS_MINUTES),
        "forecast_semantics": (
            "For every historical anchor, inspect each later one-minute candle and "
            "record whether +2% or -2% was touched first by each hourly deadline."
        ),
        "training_basis": {
            "requested_history_days": 1825,
            "source": "observed completed XRPUSDT one-minute Binance candles",
            "label_path_resolution": "one_minute_high_low_first_touch",
            "causal_feature_families": [
                "returns_and_momentum",
                "realized_volatility_and_jump_score",
                "RSI",
                "ATR_percent",
                "Bollinger_position_and_bandwidth",
                "volume_and_quote_volume",
                "trade_intensity_and_taker_buy_flow",
                "near_price_order_book_only_when_historically_available",
            ],
            "explicitly_not_used_without_point_in_time_history": [
                "total_crypto_market_cap",
                "XRP_market_cap",
                "historical_order_book_snapshots_that_were_not_collected",
            ],
        },
    }
    if not touch:
        return {
            **base,
            "status": "WAIT",
            "decision": "WAIT",
            "directional_bias": None,
            "decision_reason": "no_valid_hourly_first_touch_prediction",
            "predicted_target_price": None,
            "directional_probability": None,
            "event_probability": None,
            "no_event_probability": None,
            "expected_touch_horizon_minutes": None,
            "timeline": [],
            "promoted_for_trading": False,
        }

    envelope = _envelope_by_horizon(envelope_rows, anchor_timestamp_ms)
    timeline: list[dict[str, Any]] = []
    previous_up = 0.0
    previous_down = 0.0
    monotonicity_warnings = 0
    for row in touch:
        horizon = int(row["horizon_minutes"])
        up = _probability(row.get("p_up_02"))
        down = _probability(row.get("p_down_02"))
        no_event = _probability(row.get("p_no_event"))
        total = up + down + no_event
        if total > 0:
            up, down, no_event = up / total, down / total, no_event / total
        if up + 1e-9 < previous_up or down + 1e-9 < previous_down:
            monotonicity_warnings += 1
        previous_up = max(previous_up, up)
        previous_down = max(previous_down, down)
        event_probability = up + down
        conditional_up = up / event_probability if event_probability else 0.5
        conditional_down = down / event_probability if event_probability else 0.5
        shock = envelope.get(horizon, {})
        timeline.append(
            {
                "horizon_minutes": horizon,
                "hour": horizon // 60,
                "p_up_first_by_horizon": up,
                "p_down_first_by_horizon": down,
                "p_no_touch_by_horizon": no_event,
                "event_probability": event_probability,
                "conditional_up_given_touch": conditional_up,
                "conditional_down_given_touch": conditional_down,
                "directional_bias": (
                    "LONG" if conditional_up > conditional_down else
                    "SHORT" if conditional_down > conditional_up else
                    "NEUTRAL"
                ),
                "predicted_high_price_q50": _finite_float(
                    shock.get("max_price_q50")
                ),
                "predicted_low_price_q50": _finite_float(
                    shock.get("min_price_q50")
                ),
            }
        )

    selected = timeline[-1]
    up = float(selected["p_up_first_by_horizon"])
    down = float(selected["p_down_first_by_horizon"])
    no_event = float(selected["p_no_touch_by_horizon"])
    event_probability = up + down
    if up == down:
        bias = "NEUTRAL"
        directional_probability = 0.5
    elif up > down:
        bias = "LONG"
        directional_probability = up / event_probability if event_probability else 0.5
    else:
        bias = "SHORT"
        directional_probability = down / event_probability if event_probability else 0.5

    decision = bias
    reason = "directional_2pct_first_touch_edge"
    if bias == "NEUTRAL":
        decision = "WAIT"
        reason = "directional_probabilities_tied"
    elif event_probability < MIN_EVENT_PROBABILITY:
        decision = "WAIT"
        reason = "2pct_touch_probability_too_low_within_available_hours"
    elif directional_probability < MIN_CONDITIONAL_DIRECTION_CONFIDENCE:
        decision = "WAIT"
        reason = "directional_edge_too_small"

    probability_key = (
        "p_up_first_by_horizon" if bias == "LONG" else "p_down_first_by_horizon"
    )
    expected_horizon = (
        None
        if bias == "NEUTRAL"
        else _arrival_window(timeline, probability_key)
    )
    return {
        **base,
        "status": "READY",
        "decision": decision,
        "directional_bias": bias,
        "decision_reason": reason,
        "selected_horizon_minutes": int(selected["horizon_minutes"]),
        "predicted_target_price": (
            upper_price if bias == "LONG" else lower_price if bias == "SHORT" else None
        ),
        "directional_probability": directional_probability,
        "unconditional_up_probability": up,
        "unconditional_down_probability": down,
        "event_probability": event_probability,
        "no_event_probability": no_event,
        "expected_touch_horizon_minutes": expected_horizon,
        "timeline": timeline,
        "independent_horizon_monotonicity_warning_count": monotonicity_warnings,
        "confidence_thresholds": {
            "minimum_event_probability": MIN_EVENT_PROBABILITY,
            "minimum_conditional_direction_confidence": (
                MIN_CONDITIONAL_DIRECTION_CONFIDENCE
            ),
        },
        "promoted_for_trading": False,
    }


__all__ = [
    "MIN_CONDITIONAL_DIRECTION_CONFIDENCE",
    "MIN_EVENT_PROBABILITY",
    "build_directional_forecast",
]
