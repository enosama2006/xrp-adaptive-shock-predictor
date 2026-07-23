"""Model B gate v5: independent events and independent period performance.

A horizon is publishable for research only when:
1. multiple purged walk-forward test periods contain enough independent +10%
   and -10% event clusters;
2. at least two of those periods independently pass the 85% high-confidence
   directional precision gate with support for both directions; and
3. the final untouched temporal test also passes the same directional gate.

NO_EVENT accuracy never qualifies a directional horizon, and no model is
promoted for trading.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline

from .baseline import (
    ALLOWED_LABELS,
    EVENT_LABELS,
    BaselineConfig,
    BaselineReport,
    _build_pipeline,
    _calibrate_prefit_model,
    _directional_gate,
    _expected_calibration_error,
    train_multinomial_baseline,
)
from .walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    audit_directional_support_gate,
    build_purged_walk_forward_folds,
)

FIRST_TOUCH_GATE_VERSION = (
    "first-touch-independent-event-and-period-performance-gate-v5"
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


def _walk_forward_config(config: BaselineConfig) -> WalkForwardConfig:
    return WalkForwardConfig(
        n_folds=config.walk_forward_folds,
        initial_train_fraction=config.walk_forward_initial_train_fraction,
        calibration_fraction=config.walk_forward_calibration_fraction,
        test_fraction=config.walk_forward_test_fraction,
        step_fraction=config.walk_forward_step_fraction,
        label_horizon_ms=config.label_horizon_ms,
        embargo_ms=config.embargo_ms,
        minimum_rows_per_partition=config.walk_forward_minimum_rows_per_partition,
    )


def _build_support_audit(
    usable: pd.DataFrame,
    config: BaselineConfig,
) -> tuple[dict[str, Any], list[WalkForwardFold]]:
    try:
        folds = build_purged_walk_forward_folds(
            usable,
            _walk_forward_config(config),
        )
    except ValueError as exc:
        return (
            {
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
            },
            [],
        )

    audit = audit_directional_support_gate(
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
    return audit, folds


def _wait_report(
    *,
    reason: str,
    usable: pd.DataFrame,
    feature_names: list[str],
    metrics: dict[str, Any],
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
            **metrics,
        },
    )


def _evaluate_fold(
    fold: WalkForwardFold,
    feature_names: list[str],
    config: BaselineConfig,
) -> dict[str, Any]:
    if fold.train["label"].nunique() < 2:
        return {
            "fold_index": fold.fold_index,
            "status": "WAIT",
            "reason": "insufficient_train_label_diversity",
            "audit": fold.audit,
        }

    base = _build_pipeline(config)
    base.fit(fold.train[feature_names], fold.train["label"])
    fitted: Pipeline | CalibratedClassifierCV = base
    if fold.calibration["label"].nunique() >= 2:
        fitted = _calibrate_prefit_model(
            base,
            fold.calibration[feature_names],
            fold.calibration["label"],
        )

    probabilities = fitted.predict_proba(fold.test[feature_names])
    predictions = np.asarray(fitted.predict(fold.test[feature_names]))
    classes = [str(value) for value in fitted.classes_]
    actual = fold.test["label"].to_numpy()
    passed, reason, metrics = _directional_gate(
        predictions=predictions,
        probabilities=probabilities,
        classes=classes,
        actual=actual,
        test=fold.test,
        config=config,
    )
    metrics["expected_calibration_error"] = _expected_calibration_error(
        probabilities,
        predictions,
        actual,
        config.calibration_bins,
    )
    metrics["probability_sum_max_error"] = float(
        np.abs(probabilities.sum(axis=1) - 1.0).max(initial=0.0)
    )
    return {
        "fold_index": fold.fold_index,
        "status": "PASS" if passed else "WAIT",
        "reason": reason,
        "audit": fold.audit,
        "metrics": metrics,
    }


def _evaluate_eligible_periods(
    folds: list[WalkForwardFold],
    support_audit: dict[str, Any],
    feature_names: list[str],
    config: BaselineConfig,
) -> dict[str, Any]:
    eligible = {
        int(value) for value in support_audit.get("eligible_fold_indices", [])
    }
    results = [
        _evaluate_fold(fold, feature_names, config)
        for fold in folds
        if fold.fold_index in eligible
    ]
    passing = [
        int(result["fold_index"])
        for result in results
        if result.get("status") == "PASS"
    ]
    required = config.minimum_eligible_walk_forward_folds
    return {
        "status": "PASS" if len(passing) >= required else "WAIT",
        "reason": (
            "multiple_untouched_periods_pass_directional_precision_gate"
            if len(passing) >= required
            else "insufficient_walk_forward_directional_performance_folds"
        ),
        "methodology": "fresh-fit-per-fold-directional-performance-v1",
        "evaluated_fold_count": len(results),
        "passing_fold_count": len(passing),
        "minimum_passing_folds": required,
        "passing_fold_indices": passing,
        "folds": results,
        "note": (
            "Each fold is fitted only on its own earlier train partition, calibrated "
            "on its own later calibration partition, and evaluated on its untouched test."
        ),
    }


def train_first_touch_v5(
    dataset: pd.DataFrame,
    feature_names: list[str],
    config: BaselineConfig = BaselineConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, BaselineReport]:
    required = {"anchor_timestamp_ms", "label", "status", *feature_names}
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"baseline dataset missing columns: {sorted(missing)}")

    usable = _usable(dataset, config)
    if len(usable) < config.minimum_rows:
        return None, _wait_report(
            reason="insufficient_final_rows",
            usable=usable,
            feature_names=feature_names,
            metrics={"minimum_rows": config.minimum_rows},
        )

    support_audit, folds = _build_support_audit(usable, config)
    if support_audit.get("status") != "PASS":
        return None, _wait_report(
            reason=str(
                support_audit.get(
                    "reason",
                    "insufficient_independent_directional_events_across_untouched_periods",
                )
            ),
            usable=usable,
            feature_names=feature_names,
            metrics={"walk_forward_support_audit": support_audit},
        )

    performance_audit = _evaluate_eligible_periods(
        folds,
        support_audit,
        feature_names,
        config,
    )
    if performance_audit.get("status") != "PASS":
        return None, _wait_report(
            reason="insufficient_walk_forward_directional_performance_folds",
            usable=usable,
            feature_names=feature_names,
            metrics={
                "walk_forward_support_audit": support_audit,
                "walk_forward_performance_audit": performance_audit,
            },
        )

    model, final_report = train_multinomial_baseline(
        dataset,
        feature_names,
        config,
    )
    metrics = dict(final_report.metrics)
    metrics["legacy_inner_gate_methodology_version"] = metrics.get(
        "gate_methodology_version"
    )
    metrics["gate_methodology_version"] = FIRST_TOUCH_GATE_VERSION
    metrics["walk_forward_support_audit"] = support_audit
    metrics["walk_forward_performance_audit"] = performance_audit
    metrics["independent_event_cluster_separation_ms"] = config.label_horizon_ms
    metrics["minimum_independent_event_clusters_per_class"] = (
        MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS
    )
    upgraded = replace(final_report, metrics=metrics)
    return model, upgraded


__all__ = [
    "FIRST_TOUCH_GATE_VERSION",
    "MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS",
    "train_first_touch_v5",
]
