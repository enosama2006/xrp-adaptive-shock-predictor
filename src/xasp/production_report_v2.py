"""Extended-horizon production monitoring built on observed outcomes only."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .horizons import RESEARCH_HORIZON_SET_VERSION, RESEARCH_HORIZONS_MINUTES
from .production_report import (
    ProductionReportPaths,
    save_production_report,
)
from .production_report import (
    build_production_report as _build_legacy_report,
)

REQUIRED_ENVELOPE_ROWS_PER_HORIZON = 100
REQUIRED_MARGINAL_INTERVAL_COVERAGE = 0.85


def _complete_first_touch_horizons(section: dict[str, Any]) -> None:
    per_horizon = section.setdefault("per_horizon", {})
    for horizon in RESEARCH_HORIZONS_MINUTES:
        per_horizon.setdefault(
            str(horizon),
            {
                "status": "WAIT",
                "reason": "no_matured_predictions_for_horizon",
                "evaluated_rows": 0,
                "directional_high_confidence_rows": 0,
                "directional_high_confidence_precision": None,
            },
        )


def _complete_envelope_horizons(section: dict[str, Any]) -> None:
    per_horizon = section.setdefault("per_horizon", {})
    ready_horizons: list[int] = []
    monitoring_horizons: list[int] = []
    drift_horizons: list[int] = []

    for horizon in RESEARCH_HORIZONS_MINUTES:
        key = str(horizon)
        payload = per_horizon.get(key)
        if not isinstance(payload, dict):
            per_horizon[key] = {
                "status": "WAIT",
                "reason": "no_matured_predictions_for_horizon",
                "evaluated_rows": 0,
                "required_rows": REQUIRED_ENVELOPE_ROWS_PER_HORIZON,
            }
            monitoring_horizons.append(horizon)
            continue

        rows = int(payload.get("evaluated_rows", 0))
        max_coverage = payload.get("max_interval_coverage")
        min_coverage = payload.get("min_interval_coverage")
        enough = rows >= REQUIRED_ENVELOPE_ROWS_PER_HORIZON
        coverage_passed = (
            max_coverage is not None
            and min_coverage is not None
            and float(max_coverage) >= REQUIRED_MARGINAL_INTERVAL_COVERAGE
            and float(min_coverage) >= REQUIRED_MARGINAL_INTERVAL_COVERAGE
        )
        if not enough:
            payload["status"] = "MONITORING"
            payload["reason"] = "insufficient_matured_predictions_for_horizon"
            monitoring_horizons.append(horizon)
        elif coverage_passed:
            payload["status"] = "READY"
            payload["reason"] = "horizon_interval_coverage_gate_passed"
            ready_horizons.append(horizon)
        else:
            payload["status"] = "DRIFT_ALERT"
            payload["reason"] = "horizon_interval_coverage_below_required_85pct"
            drift_horizons.append(horizon)

    section["configured_horizons"] = list(RESEARCH_HORIZONS_MINUTES)
    section["ready_horizons"] = ready_horizons
    section["monitoring_horizons"] = monitoring_horizons
    section["drift_horizons"] = drift_horizons
    if len(ready_horizons) == len(RESEARCH_HORIZONS_MINUTES):
        section["status"] = "READY"
        section["reason"] = "all_horizon_interval_coverage_gates_passed"
    elif ready_horizons:
        section["status"] = "PARTIAL_MONITORING"
        section["reason"] = "some_horizons_ready_others_monitoring_or_drift"
    elif drift_horizons:
        section["status"] = "DRIFT_ALERT"
        section["reason"] = "no_horizon_ready_and_some_horizons_below_coverage"
    else:
        section["status"] = "MONITORING"
        section["reason"] = "insufficient_matured_predictions_across_horizons"


def build_production_report(
    *,
    ledger: pd.DataFrame,
    envelope_predictions: pd.DataFrame,
    prices: pd.DataFrame,
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    report = _build_legacy_report(
        ledger=ledger,
        envelope_predictions=envelope_predictions,
        prices=prices,
        runtime_status=runtime_status,
    )
    report["horizon_set_version"] = RESEARCH_HORIZON_SET_VERSION
    report["configured_horizons_minutes"] = list(RESEARCH_HORIZONS_MINUTES)

    first_touch = report.setdefault("first_touch", {})
    envelope = report.setdefault("future_envelope", {})
    _complete_first_touch_horizons(first_touch)
    _complete_envelope_horizons(envelope)

    first_touch_ready = first_touch.get("status") == "READY"
    envelope_ready = envelope.get("status") == "READY"
    any_research_evidence = bool(envelope.get("ready_horizons")) or int(
        first_touch.get("evaluated_rows", 0)
    ) > 0
    if first_touch_ready and envelope_ready:
        report["research_monitoring_readiness"] = "RESEARCH_MONITORING_READY"
    elif any_research_evidence:
        report["research_monitoring_readiness"] = "PARTIAL_RESEARCH_MONITORING"
    else:
        report["research_monitoring_readiness"] = "WAIT"
    report["trading_readiness"] = "WAIT"
    report["note"] = (
        "Every 15/30/45/60/120/180/240/480-minute horizon is monitored "
        "independently. A long horizon may be research-ready while a short rare-event "
        "horizon remains WAIT. No metric guarantees profit."
    )
    return report


__all__ = [
    "ProductionReportPaths",
    "build_production_report",
    "save_production_report",
]
