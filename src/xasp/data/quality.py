from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .contracts import MarketRecord


@dataclass(frozen=True)
class QualityReport:
    total: int
    valid: int
    invalid: int
    duplicates: int
    out_of_order: int
    negative_latency: int
    excessive_latency: int
    sequence_gaps: int
    reasons: dict[str, int]

    @property
    def passed(self) -> bool:
        return (
            self.invalid == 0
            and self.duplicates == 0
            and self.out_of_order == 0
            and self.negative_latency == 0
            and self.sequence_gaps == 0
        )


def _identity(record: MarketRecord) -> tuple[object, ...]:
    return (
        record.venue,
        record.symbol,
        record.record_type,
        record.event_time_ms,
        record.source_sequence,
        tuple(sorted((key, repr(value)) for key, value in record.payload.items())),
    )


def validate_records(
    records: Iterable[MarketRecord], *, max_latency_ms: int = 10_000
) -> QualityReport:
    rows = list(records)
    reasons: Counter[str] = Counter()
    seen: set[tuple[object, ...]] = set()
    duplicates = 0
    out_of_order = 0
    negative_latency = 0
    excessive_latency = 0
    sequence_gaps = 0
    previous_time: dict[tuple[str, str, str], int] = {}
    previous_sequence: dict[tuple[str, str, str], int] = {}

    for row in rows:
        key = (row.venue, row.symbol, row.record_type)
        identity = _identity(row)
        if identity in seen:
            duplicates += 1
            reasons["duplicate"] += 1
        seen.add(identity)

        prior_time = previous_time.get(key)
        if prior_time is not None and row.event_time_ms < prior_time:
            out_of_order += 1
            reasons["out_of_order"] += 1
        previous_time[key] = row.event_time_ms

        if row.latency_ms < 0:
            negative_latency += 1
            reasons["negative_latency"] += 1
        elif row.latency_ms > max_latency_ms:
            excessive_latency += 1
            reasons["excessive_latency"] += 1

        if row.source_sequence is not None:
            prior_sequence = previous_sequence.get(key)
            if prior_sequence is not None and row.source_sequence > prior_sequence + 1:
                sequence_gaps += row.source_sequence - prior_sequence - 1
                reasons["sequence_gap"] += row.source_sequence - prior_sequence - 1
            previous_sequence[key] = row.source_sequence

    invalid = duplicates + out_of_order + negative_latency + sequence_gaps
    return QualityReport(
        total=len(rows),
        valid=max(0, len(rows) - invalid),
        invalid=invalid,
        duplicates=duplicates,
        out_of_order=out_of_order,
        negative_latency=negative_latency,
        excessive_latency=excessive_latency,
        sequence_gaps=sequence_gaps,
        reasons=dict(reasons),
    )
