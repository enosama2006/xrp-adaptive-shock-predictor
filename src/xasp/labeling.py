from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


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
class CandlePoint:
    """One completed candle available at ``timestamp_ms``."""

    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("candle prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("candle high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("candle low is inconsistent")


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


def _result(
    *,
    label: BarrierLabel,
    anchor: PricePoint,
    horizon_end: int,
    touch_timestamp_ms: int | None,
    touch_price: float | None,
    mfe: float | None,
    mae: float | None,
    reason: str,
) -> FirstTouchResult:
    return FirstTouchResult(
        label=label,
        anchor_timestamp_ms=anchor.timestamp_ms,
        anchor_price=anchor.price,
        horizon_end_ms=horizon_end,
        touch_timestamp_ms=touch_timestamp_ms,
        touch_price=touch_price,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        reason=reason,
    )


def label_first_touch(
    anchor: PricePoint,
    future_path: Iterable[PricePoint],
    config: BarrierConfig = BarrierConfig(),
) -> FirstTouchResult:
    """Label first touch from point observations.

    This compatibility path is appropriate only when each point represents an
    actual ordered trade/quote observation. Minute-candle research must use
    :func:`label_first_touch_candles` so intraminute highs/lows are not lost.
    """

    horizon_end = anchor.timestamp_ms + config.horizon_ms
    points = [
        point
        for point in future_path
        if anchor.timestamp_ms < point.timestamp_ms <= horizon_end
    ]
    points.sort(key=lambda point: point.timestamp_ms)

    if not points:
        return _result(
            label=BarrierLabel.INCOMPLETE,
            anchor=anchor,
            horizon_end=horizon_end,
            touch_timestamp_ms=None,
            touch_price=None,
            mfe=None,
            mae=None,
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
            return _result(
                label=BarrierLabel.AMBIGUOUS,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=None,
                mfe=mfe,
                mae=mae,
                reason="both_barriers_observed_at_same_timestamp",
            )
        if upper_hits:
            return _result(
                label=BarrierLabel.UP_10,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=upper_hits[0].price,
                mfe=mfe,
                mae=mae,
                reason="upper_barrier_touched_first",
            )
        if lower_hits:
            return _result(
                label=BarrierLabel.DOWN_10,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=timestamp_ms,
                touch_price=lower_hits[0].price,
                mfe=mfe,
                mae=mae,
                reason="lower_barrier_touched_first",
            )

    complete = points[-1].timestamp_ms == horizon_end
    return _result(
        label=BarrierLabel.NO_EVENT if complete else BarrierLabel.INCOMPLETE,
        anchor=anchor,
        horizon_end=horizon_end,
        touch_timestamp_ms=None,
        touch_price=None,
        mfe=mfe,
        mae=mae,
        reason="neither_barrier_touched" if complete else "path_did_not_reach_horizon_end",
    )


def label_first_touch_candles(
    anchor: PricePoint,
    future_path: Iterable[CandlePoint],
    config: BarrierConfig = BarrierConfig(),
    *,
    cadence_ms: int = 60_000,
) -> FirstTouchResult:
    """Label first touch from completed OHLC candles without inventing order.

    The full expected candle sequence is required. High/low determine whether a
    barrier traded inside a candle. If both barriers trade in one candle, their
    order is unknowable at minute granularity and the result is ``AMBIGUOUS``.
    """

    if cadence_ms <= 0:
        raise ValueError("cadence_ms must be positive")
    if config.horizon_ms % cadence_ms != 0:
        raise ValueError("horizon_ms must be divisible by cadence_ms")

    horizon_end = anchor.timestamp_ms + config.horizon_ms
    candles = [
        candle
        for candle in future_path
        if anchor.timestamp_ms < candle.timestamp_ms <= horizon_end
    ]
    candles.sort(key=lambda candle: candle.timestamp_ms)

    highs = [(candle.high / anchor.price) - 1 for candle in candles]
    lows = [(candle.low / anchor.price) - 1 for candle in candles]
    mfe = max(highs) if highs else None
    mae = min(lows) if lows else None

    actual_timestamps = [candle.timestamp_ms for candle in candles]
    expected_timestamps = list(
        range(anchor.timestamp_ms + cadence_ms, horizon_end + 1, cadence_ms)
    )
    if actual_timestamps != expected_timestamps:
        reason = (
            "duplicate_or_out_of_order_candles"
            if len(actual_timestamps) != len(set(actual_timestamps))
            else "incomplete_or_gapped_candle_path"
        )
        return _result(
            label=BarrierLabel.INCOMPLETE,
            anchor=anchor,
            horizon_end=horizon_end,
            touch_timestamp_ms=None,
            touch_price=None,
            mfe=mfe,
            mae=mae,
            reason=reason,
        )

    upper_price = anchor.price * (1 + config.upper_return)
    lower_price = anchor.price * (1 + config.lower_return)

    for candle in candles:
        upper_hit = candle.high >= upper_price
        lower_hit = candle.low <= lower_price
        if upper_hit and lower_hit:
            return _result(
                label=BarrierLabel.AMBIGUOUS,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=candle.timestamp_ms,
                touch_price=None,
                mfe=mfe,
                mae=mae,
                reason="both_barriers_touched_within_same_candle",
            )
        if upper_hit:
            return _result(
                label=BarrierLabel.UP_10,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=candle.timestamp_ms,
                touch_price=upper_price,
                mfe=mfe,
                mae=mae,
                reason="upper_barrier_touched_first_by_candle_high",
            )
        if lower_hit:
            return _result(
                label=BarrierLabel.DOWN_10,
                anchor=anchor,
                horizon_end=horizon_end,
                touch_timestamp_ms=candle.timestamp_ms,
                touch_price=lower_price,
                mfe=mfe,
                mae=mae,
                reason="lower_barrier_touched_first_by_candle_low",
            )

    return _result(
        label=BarrierLabel.NO_EVENT,
        anchor=anchor,
        horizon_end=horizon_end,
        touch_timestamp_ms=None,
        touch_price=None,
        mfe=mfe,
        mae=mae,
        reason="neither_barrier_touched_in_complete_candle_path",
    )
