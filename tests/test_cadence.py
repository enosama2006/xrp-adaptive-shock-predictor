import pytest

from xasp.cadence import CadencePolicy


def test_official_anchor_is_every_minute_not_top_of_hour() -> None:
    policy = CadencePolicy()
    assert policy.official_anchor_timestamp_ms(10 * 60_000 + 42_000) == 10 * 60_000


def test_next_anchor_advances_one_minute_at_a_time() -> None:
    policy = CadencePolicy()
    assert policy.next_official_anchor_ms(10 * 60_000, 10 * 60_000 + 59_999) is None
    assert policy.next_official_anchor_ms(10 * 60_000, 11 * 60_000) == 11 * 60_000


def test_horizons_roll_from_each_anchor() -> None:
    policy = CadencePolicy()
    anchor = 11 * 60_000
    assert policy.horizon_end_ms(anchor, 15) == 26 * 60_000
    assert policy.horizon_end_ms(anchor, 60) == 71 * 60_000


def test_invalid_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be slower"):
        CadencePolicy(feature_refresh_ms=120_000, prediction_cadence_ms=60_000)
