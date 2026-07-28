"""Human-readable eight-hour XRP price path and ±2% first-touch forecast.

The close-price quantile models provide the hourly path. One joint competing-
risk model provides the coherent direction/arrival distribution. This module
combines those outputs without overriding the empirically governed decision
stored in the prediction ledger.
"""

from __future__ import annotations

import math
from typing import Any

from .horizons import RESEARCH_HORIZONS_MINUTES
from .target_definition import TARGET_DOWN_RETURN, TARGET_UP_RETURN


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
            "Predict Q05/Q50/Q95 close prices at exact hourly boundaries and derive "
            "coherent cumulative +2%/-2% first-touch probabilities from one joint "
            "event-direction/time distribution."
        ),
        "training_basis": {
            "requested_history_days": 1825,
            "source": "observed completed XRPUSDT one-minute Binance candles",
            "label_path_resolution": "one_minute_high_low_first_touch",
            "causal_feature_families": [
                "returns_and_momentum",
                "multi_scale_context_through_8_hours",
                "realized_volatility_and_jump_score",
                "RSI",
                "ATR_percent",
                "Bollinger_position_and_bandwidth",
                "volume_and_quote_volume",
                "trade_intensity_and_taker_buy_flow",
                "UTC_intraday_and_weekday_cycle",
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
            "predicted_close_price_8h_q05": None,
            "predicted_close_price_8h_q50": None,
            "predicted_close_price_8h_q95": None,
            "predicted_high_price_8h_q50": None,
            "predicted_low_price_8h_q50": None,
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
                "predicted_close_price_q05": _finite_float(
                    shock.get("close_price_q05")
                ),
                "predicted_close_price_q50": _finite_float(
                    shock.get("close_price_q50")
                ),
                "predicted_close_price_q95": _finite_float(
                    shock.get("close_price_q95")
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

    governed_decision = str(touch[-1].get("decision", "WAIT"))
    decision = (
        governed_decision
        if governed_decision in {"LONG", "SHORT", "WAIT"}
        else "WAIT"
    )
    reason = str(
        touch[-1].get(
            "decision_reason",
            "joint_forecast_available_advisory_gate_wait",
        )
    )
    if bias == "NEUTRAL":
        decision = "WAIT"
        reason = "directional_probabilities_tied"

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
        "predicted_close_price_8h_q05": selected["predicted_close_price_q05"],
        "predicted_close_price_8h_q50": selected["predicted_close_price_q50"],
        "predicted_close_price_8h_q95": selected["predicted_close_price_q95"],
        "predicted_high_price_8h_q50": selected["predicted_high_price_q50"],
        "predicted_low_price_8h_q50": selected["predicted_low_price_q50"],
        "directional_probability": directional_probability,
        "unconditional_up_probability": up,
        "unconditional_down_probability": down,
        "event_probability": event_probability,
        "no_event_probability": no_event,
        "expected_touch_horizon_minutes": expected_horizon,
        "timeline": timeline,
        "independent_horizon_monotonicity_warning_count": monotonicity_warnings,
        "probability_coherence": (
            "PASS"
            if monotonicity_warnings == 0
            else "LEGACY_INDEPENDENT_HORIZON_WARNING"
        ),
        "price_path_available": any(
            row["predicted_close_price_q50"] is not None for row in timeline
        ),
        "price_path_semantics": (
            "Q05/Q50/Q95 are predicted closing prices at each exact hourly "
            "boundary; predicted high/low are intrahorizon excursions."
        ),
        "decision_scope": "research_advisory_not_order_execution",
        "decision_policy": {
            "source": "validation_selected_then_verified_on_untouched_test",
            "applied_result": decision,
            "applied_reason": reason,
        },
        "promoted_for_trading": False,
    }


__all__ = ["build_directional_forecast"]
