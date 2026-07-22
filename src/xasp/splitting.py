from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeInterval:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")

    def overlaps(self, other: "TimeInterval") -> bool:
        return self.start_ms < other.end_ms and other.start_ms < self.end_ms


@dataclass(frozen=True)
class PurgedSplit:
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    embargoed_indices: tuple[int, ...]


def build_purged_split(
    label_windows: list[TimeInterval],
    validation_interval: TimeInterval,
    embargo_ms: int,
) -> PurgedSplit:
    """Build one chronological split with overlap purge and post-validation embargo.

    Samples whose label windows overlap the validation period are purged. Samples
    beginning during the embargo immediately after validation are embargoed.
    Only samples fully before validation can enter training in this first strict
    implementation; later windows belong to future folds, never the past fold.
    """

    if embargo_ms < 0:
        raise ValueError("embargo_ms must be non-negative")

    train: list[int] = []
    validation: list[int] = []
    purged: list[int] = []
    embargoed: list[int] = []
    embargo_end = validation_interval.end_ms + embargo_ms

    for index, window in enumerate(label_windows):
        if window.start_ms >= validation_interval.start_ms and window.start_ms < validation_interval.end_ms:
            validation.append(index)
            continue
        if window.overlaps(validation_interval):
            purged.append(index)
            continue
        if validation_interval.end_ms <= window.start_ms < embargo_end:
            embargoed.append(index)
            continue
        if window.end_ms <= validation_interval.start_ms:
            train.append(index)

    return PurgedSplit(
        train_indices=tuple(train),
        validation_indices=tuple(validation),
        purged_indices=tuple(purged),
        embargoed_indices=tuple(embargoed),
    )
