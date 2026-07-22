from xasp.data.contracts import MarketRecord
from xasp.data.quality import validate_records


def rec(*, t: int, received: int | None = None, seq: int | None = None) -> MarketRecord:
    return MarketRecord(
        venue="binance_spot",
        symbol="xrpusdt",
        record_type="agg_trade",
        event_time_ms=t,
        received_time_ms=t if received is None else received,
        source_sequence=seq,
        payload={"price": "1.0", "qty": "2.0"},
    )


def test_clean_records_pass() -> None:
    report = validate_records([rec(t=1, seq=1), rec(t=2, seq=2)])
    assert report.passed
    assert report.invalid == 0


def test_duplicate_and_out_of_order_fail() -> None:
    first = rec(t=2, seq=2)
    report = validate_records([first, first, rec(t=1, seq=3)])
    assert not report.passed
    assert report.duplicates == 1
    assert report.out_of_order == 1


def test_sequence_gap_and_negative_latency_fail() -> None:
    report = validate_records([rec(t=1, seq=1), rec(t=2, received=1, seq=4)])
    assert report.sequence_gaps == 2
    assert report.negative_latency == 1
    assert not report.passed
