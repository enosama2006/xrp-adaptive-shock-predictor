import pytest

from xasp.labeling import (
    BarrierConfig,
    BarrierLabel,
    PricePoint,
    label_first_touch,
)


def p(t: int, price: float) -> PricePoint:
    return PricePoint(timestamp_ms=t, price=price)


def test_default_barriers_use_the_governed_two_percent_target() -> None:
    config = BarrierConfig()

    assert config.upper_return == 0.02
    assert config.lower_return == -0.02


def test_upper_barrier_first() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 101), p(2_000, 103), p(3_600_000, 102)],
    )
    assert result.label == BarrierLabel.UP_02
    assert result.touch_timestamp_ms == 2_000


def test_lower_barrier_first() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 99), p(2_000, 97), p(3_600_000, 98)],
    )
    assert result.label == BarrierLabel.DOWN_02
    assert result.touch_timestamp_ms == 2_000


def test_no_event_requires_complete_horizon() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 101), p(3_600_000, 100.5)],
    )
    assert result.label == BarrierLabel.NO_EVENT


def test_incomplete_when_path_ends_early() -> None:
    result = label_first_touch(p(0, 100), [p(1_000, 101), p(2_000, 100.5)])
    assert result.label == BarrierLabel.INCOMPLETE


def test_ambiguous_same_timestamp_opposite_hits() -> None:
    result = label_first_touch(
        p(0, 100),
        [p(1_000, 103), p(1_000, 97), p(3_600_000, 100)],
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
    # Financial ratios are binary floating-point values; compare numerically,
    # not by exact bit-level equality. Production values retain full precision.
    assert result.max_favorable_excursion == pytest.approx(0.05, abs=1e-12)
    assert result.max_adverse_excursion == pytest.approx(-0.06, abs=1e-12)
