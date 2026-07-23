"""Horizon-aware Model B gate layered over the calibrated baseline.

The underlying classifier still performs its purged final temporal test. This
module adds a stricter pre-gate: at least two untouched walk-forward periods
must each contain enough rows and enough independent +10% and -10% event
clusters. Cluster separation equals the evaluated horizon, so overlapping
minute anchors around one shock cannot be counted as independent evidence.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline

from .baseline import (
    ALLOWED_LABELS,
    EVENT_LABELS,
    BaselineConfig,
    BaselineReport,
    train_multinomial_baseline,
)
from .walk_forward import (
    WalkForwardConfig,
    audit_directional_support_gate,
    build_purged_walk_forward_folds,
)

FIRST_TOUCH_GATE_VERSION = (
    "first-touch-purged-walk-forward-independent-event-gate-v4"
)
MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS = 3


def _usable(dataset: pd.DataFrame, config: BaselineConfig) -> pd.DataFrame:
    frame = dataset[
        (dataset["status"] == "FINAL") & dataset["label"].isin(ALLOWED_LABELS)
    ].copy()
    if "horizon_end_ms" not in frame.columns:
        frame["horizon_end_ms"] = (
            frame["anchor_timestamp_ms"].astype("int64") + config.label_horizon_ms
        )
    return frame.sort_values("anchor_timestamp_ms", ignore_index=True)


def _walk_forward_audit(
    usable: pd.DataFrame,
    config: BaselineConfig,
) -> dict[str, Any]:
    split_config = WalkForwardConfig(
        n_folds=config.walk_forward_folds,
        initial_train_fraction=config.walk_forward_initial_train_fraction,
        calibration_fraction=config.walk_forward_calibration_fraction,
        test_fraction=config.walk_forward_test_fraction,
        step_fraction=config.walk_forward_step_fraction,
        label_horizon_ms=config.label_horizon_ms,
        embargo_ms=config.embargo_ms,
        minimum_rows_per_partition=config.walk_forward_minimum_rows_per_partition,
    )
    try:
        folds = build_purged_walk_forward_folds(usable, split_config)
    except ValueError as exc:
        return {
            "status": "WAIT",
            "reason": "walk_forward_split_unavailable",
            "methodology": "purged-walk-forward-independent-event-clusters-v2",
            "error": str(exc),
            "fold_count": 0,
            "eligible_fold_count": 0,
            "minimum_eligible_folds": config.minimum_eligible_walk_forward_folds,
            "minimum_support_per_event_class": (
                config.minimum_event_test_support_per_class
            ),
            "minimum_independent_clusters_per_event_class": (
                MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS
            ),
            "cluster_separation_ms": config.label_horizon_ms,
            "folds": [],
        }
    return audit_directional_support_gate(
        folds,
        minimum_support_per_event_class=config.minimum_event_test_support_per_class,
        minimum_independent_clusters_per_event_class=(
            MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS
        ),
        minimum_eligible_folds=config.minimum_eligible_walk_forward_folds,
        cluster_separation_ms=config.label_horizon_ms,
        label_column="label",
        event_labels=EVENT_LABELS,
        event_time_column="touch_timestamp_ms",
    )


def _wait_report(
    *,
    reason: str,
    usable: pd.DataFrame,
    feature_names: list[str],
    audit: dict[str, Any],
) -> BaselineReport:
    counts = {
        label: int((usable["label"] == label).sum()) for label in ALLOWED_LABELS
    }
    return BaselineReport(
        status="WAIT",
        reason=reason,
        row_count=int(len(usable)),
        train_rows=0,
        calibration_rows=0,
        test_rows=0,
        feature_names=tuple(feature_names),
        class_counts=counts,
        metrics={
            "gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "walk_forward_support_audit": audit,
            "required_empirical_precision": config_value(
                audit, "required_empirical_precision", 0.85
            ),
        },
    )


def config_value(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    return float(value)


def train_first_touch_v4(
    dataset: pd.DataFrame,
    feature_names: list[str],
    config: BaselineConfig = BaselineConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, BaselineReport]:
    required = {"anchor_timestamp_ms", "label", "status", *feature_names}
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"baseline dataset missing columns: {sorted(missing)}")

    usable = _usable(dataset, config)
    audit = _walk_forward_audit(usable, config)
    if audit.get("status") != "PASS":
        return None, _wait_report(
            reason=str(
                audit.get(
                    "reason",
                    "insufficient_independent_directional_events_across_untouched_periods",
                )
            ),
            usable=usable,
            feature_names=feature_names,
            audit=audit,
        )

    model, report = train_multinomial_baseline(dataset, feature_names, config)
    metrics = dict(report.metrics)
    metrics["legacy_inner_gate_methodology_version"] = metrics.get(
        "gate_methodology_version"
    )
    metrics["gate_methodology_version"] = FIRST_TOUCH_GATE_VERSION
    metrics["walk_forward_support_audit"] = audit
    metrics["independent_event_cluster_separation_ms"] = config.label_horizon_ms
    metrics["minimum_independent_event_clusters_per_class"] = (
        MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS
    )
    upgraded = replace(report, metrics=metrics)
    return model, upgraded


__all__ = [
    "FIRST_TOUCH_GATE_VERSION",
    "MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS",
    "train_first_touch_v4",
]
