from __future__ import annotations

import pandas as pd
import pytest

from xasp.walk_forward import (
    WalkForwardConfig,
    audit_directional_support_gate,
    build_purged_walk_forward_folds,
    summarize_event_support,
)

MINUTE = 60_000


def _frame(rows: int = 1_000) -> pd.DataFrame:
    anchor = pd.Series(range(rows), dtype="int64") * MINUTE
    labels = ["NO_EVENT"] * rows
    labels[650] = "UP_10"
    labels[760] = "DOWN_10"
    labels[850] = "UP_10"
    labels[960] = "DOWN_10"
    return pd.DataFrame(
        {
            "anchor_timestamp_ms": anchor,
            "horizon_end_ms": anchor + 5 * MINUTE,
            "label": labels,
        }
    )


def _balanced_event_frame(rows: int = 1_000) -> pd.DataFrame:
    frame = _frame(rows)
    for start in (600, 700, 800, 900):
        for offset in range(10, 13):
            frame.loc[start + offset, "label"] = "UP_10"
        for offset in range(20, 23):
            frame.loc[start + offset, "label"] = "DOWN_10"
    return frame


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


def test_event_support_is_reported_per_untouched_test_period() -> None:
    folds = build_purged_walk_forward_folds(_frame(), _config())

    support = summarize_event_support(folds)

    assert len(support) == 4
    assert support[0]["event_support"] == {"UP_10": 1, "DOWN_10": 0}
    assert support[1]["event_support"] == {"UP_10": 0, "DOWN_10": 1}
    assert support[2]["event_support"] == {"UP_10": 1, "DOWN_10": 0}
    assert support[3]["event_support"] == {"UP_10": 0, "DOWN_10": 1}
    assert all(item["all_events_present"] is False for item in support)


def test_directional_support_gate_waits_when_events_are_split_across_periods() -> None:
    folds = build_purged_walk_forward_folds(_frame(), _config())

    audit = audit_directional_support_gate(
        folds,
        minimum_support_per_event_class=1,
        minimum_eligible_folds=2,
    )

    assert audit["status"] == "WAIT"
    assert audit["eligible_fold_count"] == 0
    assert audit["aggregate_event_support"] == {"UP_10": 2, "DOWN_10": 2}
    assert all(
        fold["eligible_for_directional_performance_evaluation"] is False
        for fold in audit["folds"]
    )


def test_directional_support_gate_passes_only_with_multiple_balanced_periods() -> None:
    folds = build_purged_walk_forward_folds(_balanced_event_frame(), _config())

    audit = audit_directional_support_gate(
        folds,
        minimum_support_per_event_class=2,
        minimum_eligible_folds=3,
    )

    assert audit["status"] == "PASS"
    assert audit["eligible_fold_count"] == 4
    assert audit["eligible_fold_indices"] == [1, 2, 3, 4]
    assert all(
        fold["eligible_for_directional_performance_evaluation"] is True
        for fold in audit["folds"]
    )


def test_walk_forward_rejects_fraction_layout_beyond_timeline() -> None:
    with pytest.raises(ValueError, match="fractions exceed"):
        WalkForwardConfig(
            n_folds=5,
            initial_train_fraction=0.60,
            calibration_fraction=0.10,
            test_fraction=0.10,
            step_fraction=0.10,
        )
