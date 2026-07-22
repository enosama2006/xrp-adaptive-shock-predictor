from xasp.labeling import (
    BarrierConfig,
    BarrierLabel,
    PricePoint,
    label_first_touch,
)


def p(t: int, price: float) -> PricePoint:
    return PricePoint(timestamp_ms=t, price=price)


def test_upper_barrier_first() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 104), p(2_000, 111), p(3_600_000, 108)],
    )
    assert result.label == BarrierLabel.UP_10
    assert result.touch_timestamp_ms == 2_000


def test_lower_barrier_first() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 97), p(2_000, 89), p(3_600_000, 95)],
    )
    assert result.label == BarrierLabel.DOWN_10
    assert result.touch_timestamp_ms == 2_000


def test_no_event_requires_complete_horizon() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 102), p(3_600_000, 101)],
    )
    assert result.label == BarrierLabel.NO_EVENT


def test_incomplete_when_path_ends_early() -> None:
    result = label_first_touch(p(0, 100), [p(1_000, 102), p(2_000, 101)])
    assert result.label == BarrierLabel.INCOMPLETE


def test_ambiguous_same_timestamp_opposite_hits() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 111), p(1_000, 89), p(3_600_000, 100)],
    )
    assert result.label == BarrierLabel.AMBIGUOUS


def test_points_after_horizon_are_ignored() -> None:
    config = BarrierConfig(horizon_ms=60_000)
    result = label_first_touch(
        p(0, 100),
        [p(60_000, 101), p(61_000, 120)],
        config,
    )
    assert result.label == BarrierLabel.NO_EVENT


def test_excursions_are_recorded() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 105), p(2_000, 94), p(3_600_000, 101)],
    )
    assert result.max_favorable_excursion == 0.05
    assert result.max_adverse_excursion == -0.06
