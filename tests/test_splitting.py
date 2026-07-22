from xasp.splitting import TimeInterval, build_purged_split


def test_overlap_is_purged_and_embargo_is_excluded() -> None:
    windows = [
        TimeInterval(0, 10),
        TimeInterval(8, 18),
        TimeInterval(10, 20),
        TimeInterval(20, 30),
        TimeInterval(30, 40),
    ]
    split = build_purged_split(
        windows,
        validation_interval=TimeInterval(10, 20),
        embargo_ms=10,
    )

    assert split.train_indices == (0,)
    assert split.purged_indices == (1,)
    assert split.validation_indices == (2,)
    assert split.embargoed_indices == (3,)
    assert 4 not in split.train_indices


def test_touching_boundaries_do_not_overlap() -> None:
    assert not TimeInterval(0, 10).overlaps(TimeInterval(10, 20))


def test_negative_embargo_is_rejected() -> None:
    try:
        build_purged_split([], TimeInterval(10, 20), -1)
    except ValueError as error:
        assert "embargo_ms" in str(error)
    else:
        raise AssertionError("negative embargo must fail")
