"""Data-driven candidate-window selection for Model B research.

The selector does not alter the configured production horizons automatically.
It reports quantile-aligned and evidence-supported candidates for human review,
so descriptive analysis can correct the modeling strategy before retraining.
"""

from __future__ import annotations

from typing import Any


QUANTILE_KEYS = ("median_minutes", "p75_minutes", "p90_minutes", "p95_minutes")


def _nearest_covering_horizon(value: Any, horizons: list[int]) -> int | None:
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return None
    return next((horizon for horizon in horizons if horizon >= minutes), None)


def select_candidate_windows(
    discovery: dict[str, Any],
    *,
    minimum_events_per_direction: int = 30,
    minimum_independent_clusters_per_direction: int = 10,
    minimum_any_touch_rate: float = 0.005,
) -> dict[str, Any]:
    if discovery.get("status") != "READY":
        return {
            "status": "WAIT",
            "reason": "first_passage_discovery_not_ready",
            "quantile_aligned_horizons_minutes": [],
            "evidence_supported_horizons_minutes": [],
            "automatic_model_reconfiguration": False,
        }

    raw_horizons = discovery.get("horizons", {})
    if not isinstance(raw_horizons, dict):
        return {
            "status": "WAIT",
            "reason": "discovery_horizon_payload_invalid",
            "quantile_aligned_horizons_minutes": [],
            "evidence_supported_horizons_minutes": [],
            "automatic_model_reconfiguration": False,
        }
    horizons = sorted(int(value) for value in raw_horizons)
    barrier = discovery.get("barrier_time_statistics", {})
    up_stats = barrier.get("UP_10", {}) if isinstance(barrier, dict) else {}
    down_stats = barrier.get("DOWN_10", {}) if isinstance(barrier, dict) else {}

    quantile_candidates: set[int] = set()
    for key in QUANTILE_KEYS:
        for statistics in (up_stats, down_stats):
            if not isinstance(statistics, dict):
                continue
            candidate = _nearest_covering_horizon(statistics.get(key), horizons)
            if candidate is not None:
                quantile_candidates.add(candidate)

    evidence_candidates: list[int] = []
    evidence: dict[str, Any] = {}
    for horizon in horizons:
        row = raw_horizons.get(str(horizon), {})
        if not isinstance(row, dict):
            continue
        upper_events = int(row.get("upper_10_reached_count", 0))
        lower_events = int(row.get("lower_10_reached_count", 0))
        upper_clusters = int(row.get("upper_independent_clusters", 0))
        lower_clusters = int(row.get("lower_independent_clusters", 0))
        touch_rate = float(row.get("any_10pct_touch_rate", 0.0))
        passed = (
            upper_events >= minimum_events_per_direction
            and lower_events >= minimum_events_per_direction
            and upper_clusters >= minimum_independent_clusters_per_direction
            and lower_clusters >= minimum_independent_clusters_per_direction
            and touch_rate >= minimum_any_touch_rate
        )
        evidence[str(horizon)] = {
            "passed": passed,
            "upper_events": upper_events,
            "lower_events": lower_events,
            "upper_independent_clusters": upper_clusters,
            "lower_independent_clusters": lower_clusters,
            "any_touch_rate": touch_rate,
        }
        if passed:
            evidence_candidates.append(horizon)

    configured_model_horizons = {15, 30, 45, 60, 120, 180, 240, 480}
    longer_supported = [
        horizon for horizon in evidence_candidates if horizon > max(configured_model_horizons)
    ]
    configured_supported = [
        horizon for horizon in evidence_candidates if horizon in configured_model_horizons
    ]
    strategy_revision_required = bool(longer_supported) and not configured_supported

    return {
        "status": "READY",
        "reason": "empirical_candidates_generated_for_human_review",
        "quantile_aligned_horizons_minutes": sorted(quantile_candidates),
        "evidence_supported_horizons_minutes": evidence_candidates,
        "configured_supported_horizons_minutes": configured_supported,
        "longer_supported_horizons_minutes": longer_supported,
        "strategy_revision_required": strategy_revision_required,
        "thresholds": {
            "minimum_events_per_direction": minimum_events_per_direction,
            "minimum_independent_clusters_per_direction": (
                minimum_independent_clusters_per_direction
            ),
            "minimum_any_touch_rate": minimum_any_touch_rate,
        },
        "evidence_by_horizon": evidence,
        "automatic_model_reconfiguration": False,
        "note": (
            "Candidates are descriptive. Horizon changes require a new purged walk-forward "
            "training and untouched-test review before any research model is promoted."
        ),
    }


__all__ = ["select_candidate_windows"]
