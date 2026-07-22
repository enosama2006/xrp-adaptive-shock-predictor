from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class BarrierLabel(StrEnum):
    UP_10 = "UP_10"
    DOWN_10 = "DOWN_10"
    NO_EVENT = "NO_EVENT"
    AMBIGUOUS = "AMBIGUOUS"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class BarrierConfig:
    upper_return: float = 0.10
    lower_return: float = -0.10
    horizon_ms: int = 60 * 60 * 1000

    def __post_init__(self) -> None:
        if self.upper_return <= 0:
            raise ValueError("upper_return must be positive")
        if self.lower_return >= 0:
            raise ValueError("lower_return must be negative")
        if self.horizon_ms <= 0:
            raise ValueError("horizon_ms must be positive")


@dataclass(frozen=True)
class PricePoint:
    timestamp_ms: int
    price: float

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if self.price <= 0:
            raise ValueError("price must be positive")


@dataclass(frozen=True)
class FirstTouchResult:
    label: BarrierLabel
    anchor_timestamp_ms: int
    anchor_price: float
    horizon_end_ms: int
    touch_timestamp_ms: int | None
    touch_price: float | None
    max_favorable_excursion: float | None
    max_adverse_excursion: float | None
    reason: str


def label_first_touch(
    anchor: PricePoint,
    future_path: Iterable[PricePoint],
    config: BarrierConfig = BarrierConfig(),
) -> FirstTouchResult:
    """Label the first +barrier/-barrier touch using only the declared horizon.

    The function is deterministic, rejects non-monotonic paths, and treats two
    points sharing the same timestamp on opposite barriers as ambiguous because
    their true within-timestamp ordering is unknowable at this granularity.
    """

    horizon_end = anchor.timestamp_ms + config.horizon_ms
    points = [
        point
        for point in future_path
        if anchor.timestamp_ms < point.timestamp_ms <= horizon_end
    ]
    points.sort(key=lambda point: point.timestamp_ms)

    if not points:
        return FirstTouchResult(
            label=BarrierLabel.INCOMPLETE,
            anchor_timestamp_ms=anchor.timestamp_ms,
            anchor_price=anchor.price,
            horizon_end_ms=horizon_end,
            touch_timestamp_ms=None,
            touch_price=None,
            max_favorable_excursion=None,
            max_adverse_excursion=None,
            reason="no_path_points_within_horizon",
        )

    upper_price = anchor.price * (1 + config.upper_return)
    lower_price = anchor.price * (1 + config.lower_return)

    returns = [(point.price / anchor.price) - 1 for point in points]
    mfe = max(returns)
    mae = min(returns)

    grouped: dict[int, list[PricePoint]] = {}
    for point in points:
        grouped.setdefault(point.timestamp_ms, []).append(point)

    for timestamp_ms in sorted(grouped):
        bucket = grouped[timestamp_ms]
        upper_hits = [point for point in bucket if point.price >= upper_price]
        lower_hits = [point for point in bucket if point.price <= lower_price]

        if upper_hits and lower_hits:
            return FirstTouchResult(
                label=BarrierLabel.AMBIGUOUS,
                anchor_timestamp_ms=anchor.timestamp_ms,
                anchor_price=anchor.price,
                horizon_end_ms=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=None,
                max_favorable_excursion=mfe,
                max_adverse_excursion=mae,
                reason="both_barriers_observed_at_same_timestamp",
            )
        if upper_hits:
            hit = upper_hits[0]
            return FirstTouchResult(
                label=BarrierLabel.UP_10,
                anchor_timestamp_ms=anchor.timestamp_ms,
                anchor_price=anchor.price,
                horizon_end_ms=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=hit.price,
                max_favorable_excursion=mfe,
                max_adverse_excursion=mae,
                reason="upper_barrier_touched_first",
            )
        if lower_hits:
            hit = lower_hits[0]
            return FirstTouchResult(
                label=BarrierLabel.DOWN_10,
                anchor_timestamp_ms=anchor.timestamp_ms,
                anchor_price=anchor.price,
                horizon_end_ms=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=hit.price,
                max_favorable_excursion=mfe,
                max_adverse_excursion=mae,
                reason="lower_barrier_touched_first",
            )

    last_timestamp = points[-1].timestamp_ms
    complete = last_timestamp == horizon_end
    return FirstTouchResult(
        label=BarrierLabel.NO_EVENT if complete else BarrierLabel.INCOMPLETE,
        anchor_timestamp_ms=anchor.timestamp_ms,
        anchor_price=anchor.price,
        horizon_end_ms=horizon_end,
        touch_timestamp_ms=None,
        touch_price=None,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        reason="neither_barrier_touched" if complete else "path_did_not_reach_horizon_end",
    )
