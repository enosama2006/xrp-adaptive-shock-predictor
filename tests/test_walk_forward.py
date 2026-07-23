from __future__ import annotations

import pandas as pd
import pytest

from xasp.walk_forward import (
    WalkForwardConfig,
    audit_directional_support_gate,
    build_purged_walk_forward_folds,
    count_independent_event_clusters,
    summarize_event_support,
)

MINUTE = 60_000


def _frame(rows: int = 1_000) -> pd.DataFrame:
    anchor = pd.Series(range(rows), dtype="int64") * MINUTE
    labels = ["NO_EVENT"] * rows
    touch: list[int | None] = [None] * rows
    for block in range(0, rows, 20):
        if block + 1 >= rows:
            continue
        labels[block] = "UP_10"
        labels[block + 1] = "DOWN_10"
        touch[block] = block * MINUTE + 3 * MINUTE
        touch[block + 1] = block * MINUTE + 4 * MINUTE
    return pd.DataFrame(
        {
            "anchor_timestamp_ms": anchor,
            "horizon_end_ms": anchor + 5 * MINUTE,
            "label": labels,
            "touch_timestamp_ms": touch,
        }
    )


def _sparse_split_event_frame(rows: int = 1_000) -> pd.DataFrame:
    anchor = pd.Series(range(rows), dtype="int64") * MINUTE
    labels = ["NO_EVENT"] * rows
    touch: list[int | None] = [None] * rows
    for index, label in ((650, "UP_10"), (760, "DOWN_10"), (850, "UP_10"), (960, "DOWN_10")):
        labels[index] = label
        touch[index] = (index + 2) * MINUTE
    return pd.DataFrame(
        {
            "anchor_timestamp_ms": anchor,
            "horizon_end_ms": anchor + 5 * MINUTE,
            "label": labels,
            "touch_timestamp_ms": touch,
        }
    )


def _config() -> WalkForwardConfig:
    return WalkForwardConfig(
        n_folds=4,
        initial_train_fraction=0.50,
        calibration_fraction=0.10,
        test_fraction=0.10,
        step_fraction=0.10,
        label_horizon_ms=5 * MINUTE,
        embargo_ms=5 * MINUTE,
        minimum_rows_per_partition=50,
    )


def test_walk_forward_folds_are_chronological_purged_and_expanding() -> None:
    folds = build_purged_walk_forward_folds(_frame(), _config())

    assert len(folds) == 4
    assert [len(fold.train) for fold in folds] == sorted(len(fold.train) for fold in folds)
    for fold in folds:
        audit = fold.audit
        assert int(fold.train["horizon_end_ms"].max()) <= audit["calibration_boundary_ms"]
        assert (
            int(fold.calibration["anchor_timestamp_ms"].min())
            >= audit["calibration_boundary_ms"] + audit["embargo_ms"]
        )
        assert int(fold.calibration["horizon_end_ms"].max()) <= audit["test_boundary_ms"]
        assert (
            int(fold.test["anchor_timestamp_ms"].min())
            >= audit["test_boundary_ms"] + audit["embargo_ms"]
        )
        assert int(fold.train["anchor_timestamp_ms"].max()) < int(
            fold.calibration["anchor_timestamp_ms"].min()
        )
        assert int(fold.calibration["anchor_timestamp_ms"].max()) < int(
            fold.test["anchor_timestamp_ms"].min()
        )


def test_event_support_reports_rows_and_independent_clusters_per_period() -> None:
    folds = build_purged_walk_forward_folds(_frame(), _config())

    support = summarize_event_support(
        folds,
        cluster_separation_ms=5 * MINUTE,
    )

    assert len(support) == 4
    for item in support:
        assert item["event_support"]["UP_10"] >= 4
        assert item["event_support"]["DOWN_10"] >= 4
        assert item["independent_event_clusters"]["UP_10"] >= 4
        assert item["independent_event_clusters"]["DOWN_10"] >= 4
        assert item["all_events_present"] is True
        assert item["all_cluster_types_present"] is True


def test_overlapping_positive_rows_count_as_one_independent_event() -> None:
    frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": [0, MINUTE, 2 * MINUTE, 20 * MINUTE],
            "label": ["UP_10", "UP_10", "UP_10", "UP_10"],
            "touch_timestamp_ms": [5 * MINUTE, 5 * MINUTE, 6 * MINUTE, 25 * MINUTE],
        }
    )

    clusters = count_independent_event_clusters(
        frame,
        label="UP_10",
        cluster_separation_ms=5 * MINUTE,
    )

    assert clusters == 2


def test_directional_support_gate_waits_when_events_are_split_across_periods() -> None:
    folds = build_purged_walk_forward_folds(_sparse_split_event_frame(), _config())

    audit = audit_directional_support_gate(
        folds,
        minimum_support_per_event_class=1,
        minimum_independent_clusters_per_event_class=1,
        minimum_eligible_folds=2,
        cluster_separation_ms=5 * MINUTE,
    )

    assert audit["status"] == "WAIT"
    assert audit["eligible_fold_count"] == 0
    assert audit["aggregate_event_support"] == {"UP_10": 2, "DOWN_10": 2}
    assert audit["aggregate_independent_event_clusters"] == {"UP_10": 2, "DOWN_10": 2}


def test_directional_support_gate_requires_multiple_independent_periods() -> None:
    folds = build_purged_walk_forward_folds(_frame(), _config())

    audit = audit_directional_support_gate(
        folds,
        minimum_support_per_event_class=3,
        minimum_independent_clusters_per_event_class=3,
        minimum_eligible_folds=2,
        cluster_separation_ms=5 * MINUTE,
    )

    assert audit["status"] == "PASS"
    assert audit["eligible_fold_count"] == 4
    assert audit["eligible_fold_indices"] == [1, 2, 3, 4]
    assert audit["aggregate_independent_event_clusters"]["UP_10"] >= 16
    assert audit["aggregate_independent_event_clusters"]["DOWN_10"] >= 16


def test_walk_forward_rejects_fraction_layout_beyond_timeline() -> None:
    with pytest.raises(ValueError, match="fractions exceed"):
        WalkForwardConfig(
            n_folds=5,
            initial_train_fraction=0.60,
            calibration_fraction=0.10,
            test_fraction=0.10,
            step_fraction=0.10,
        )
