"""Purged expanding-window walk-forward splits for rare-event research.

The splitter is model-agnostic. It receives already point-in-time rows and
returns chronological train/calibration/test folds with horizon purge and
embargo applied at every boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    n_folds: int = 4
    initial_train_fraction: float = 0.50
    calibration_fraction: float = 0.10
    test_fraction: float = 0.10
    step_fraction: float = 0.10
    label_horizon_ms: int = 60_000
    embargo_ms: int | None = None
    minimum_rows_per_partition: int = 30

    def __post_init__(self) -> None:
        if self.n_folds < 2:
            raise ValueError("n_folds must be at least two")
        for name in (
            "initial_train_fraction",
            "calibration_fraction",
            "test_fraction",
            "step_fraction",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be in (0, 1)")
        final_end = (
            self.initial_train_fraction
            + self.calibration_fraction
            + self.test_fraction
            + self.step_fraction * (self.n_folds - 1)
        )
        if final_end > 1.0 + 1e-12:
            raise ValueError("walk-forward fractions exceed the available timeline")
        if self.label_horizon_ms <= 0:
            raise ValueError("label_horizon_ms must be positive")
        if self.embargo_ms is not None and self.embargo_ms < 0:
            raise ValueError("embargo_ms must be non-negative")
        if self.minimum_rows_per_partition < 1:
            raise ValueError("minimum_rows_per_partition must be positive")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_index: int
    train: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame
    audit: dict[str, int]


REQUIRED_TIME_COLUMNS = {"anchor_timestamp_ms", "horizon_end_ms"}


def build_purged_walk_forward_folds(
    frame: pd.DataFrame,
    config: WalkForwardConfig,
) -> list[WalkForwardFold]:
    """Build expanding train windows and untouched chronological test periods."""

    missing = REQUIRED_TIME_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"walk-forward dataset missing columns: {sorted(missing)}")
    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True).copy()
    if ordered.empty:
        return []

    row_count = len(ordered)
    embargo_ms = config.label_horizon_ms if config.embargo_ms is None else config.embargo_ms
    folds: list[WalkForwardFold] = []

    for fold_index in range(config.n_folds):
        train_fraction = config.initial_train_fraction + config.step_fraction * fold_index
        train_end = int(row_count * train_fraction)
        calibration_end = train_end + int(row_count * config.calibration_fraction)
        test_end = calibration_end + int(row_count * config.test_fraction)
        if train_end <= 0 or calibration_end <= train_end or test_end <= calibration_end:
            raise ValueError("walk-forward fractions produced an empty raw partition")
        if test_end > row_count:
            raise ValueError("walk-forward fold exceeds the available timeline")

        calibration_boundary_ms = int(ordered.iloc[train_end]["anchor_timestamp_ms"])
        test_boundary_ms = int(ordered.iloc[calibration_end]["anchor_timestamp_ms"])

        raw_train = ordered.iloc[:train_end]
        raw_calibration = ordered.iloc[train_end:calibration_end]
        raw_test = ordered.iloc[calibration_end:test_end]

        train = raw_train[
            raw_train["horizon_end_ms"] <= calibration_boundary_ms
        ].copy()
        calibration = raw_calibration[
            (
                raw_calibration["anchor_timestamp_ms"]
                >= calibration_boundary_ms + embargo_ms
            )
            & (raw_calibration["horizon_end_ms"] <= test_boundary_ms)
        ].copy()
        test = raw_test[
            raw_test["anchor_timestamp_ms"] >= test_boundary_ms + embargo_ms
        ].copy()

        if min(len(train), len(calibration), len(test)) < config.minimum_rows_per_partition:
            raise ValueError(
                "walk-forward fold has insufficient rows after purge and embargo: "
                f"fold={fold_index + 1}, train={len(train)}, "
                f"calibration={len(calibration)}, test={len(test)}"
            )

        audit = {
            "fold_index": fold_index + 1,
            "raw_train_rows": int(len(raw_train)),
            "raw_calibration_rows": int(len(raw_calibration)),
            "raw_test_rows": int(len(raw_test)),
            "train_rows": int(len(train)),
            "calibration_rows": int(len(calibration)),
            "test_rows": int(len(test)),
            "purged_train_rows": int(len(raw_train) - len(train)),
            "purged_or_embargoed_calibration_rows": int(
                len(raw_calibration) - len(calibration)
            ),
            "embargoed_test_rows": int(len(raw_test) - len(test)),
            "calibration_boundary_ms": calibration_boundary_ms,
            "test_boundary_ms": test_boundary_ms,
            "label_horizon_ms": int(config.label_horizon_ms),
            "embargo_ms": int(embargo_ms),
            "train_start_ms": int(train["anchor_timestamp_ms"].min()),
            "train_end_ms": int(train["anchor_timestamp_ms"].max()),
            "calibration_start_ms": int(calibration["anchor_timestamp_ms"].min()),
            "calibration_end_ms": int(calibration["anchor_timestamp_ms"].max()),
            "test_start_ms": int(test["anchor_timestamp_ms"].min()),
            "test_end_ms": int(test["anchor_timestamp_ms"].max()),
        }
        folds.append(
            WalkForwardFold(
                fold_index=fold_index + 1,
                train=train,
                calibration=calibration,
                test=test,
                audit=audit,
            )
        )

    return folds


def summarize_event_support(
    folds: list[WalkForwardFold],
    *,
    label_column: str = "label",
    event_labels: tuple[str, ...] = ("UP_10", "DOWN_10"),
) -> list[dict[str, Any]]:
    """Report rare-event support independently for every untouched test fold."""

    summaries: list[dict[str, Any]] = []
    for fold in folds:
        if label_column not in fold.test.columns:
            raise ValueError(f"walk-forward test fold missing label column: {label_column}")
        counts = {
            label: int((fold.test[label_column] == label).sum()) for label in event_labels
        }
        summaries.append(
            {
                "fold_index": fold.fold_index,
                "test_rows": int(len(fold.test)),
                "event_support": counts,
                "all_events_present": all(value > 0 for value in counts.values()),
                "test_start_ms": fold.audit["test_start_ms"],
                "test_end_ms": fold.audit["test_end_ms"],
            }
        )
    return summaries


__all__ = [
    "WalkForwardConfig",
    "WalkForwardFold",
    "build_purged_walk_forward_folds",
    "summarize_event_support",
]
