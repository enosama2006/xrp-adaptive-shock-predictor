from __future__ import annotations

from types import SimpleNamespace

from xasp.pipeline import _normalize_completed_minute_timestamp, _records_to_prices


def test_binance_close_timestamp_is_normalized_to_availability_boundary() -> None:
    assert _normalize_completed_minute_timestamp(59_999) == 60_000
    assert _normalize_completed_minute_timestamp(119_999) == 120_000
    assert _normalize_completed_minute_timestamp(120_000) == 120_000


def test_kline_trade_flow_fields_are_preserved() -> None:
    record = SimpleNamespace(
        event_time_ms=59_999,
        payload={
            "close_time_ms": 59_999,
            "open": "1.0",
            "high": "1.2",
            "low": "0.9",
            "close": "1.1",
            "volume": "100",
            "quote_volume": "110",
            "trade_count": 25,
            "taker_buy_base": "60",
            "taker_buy_quote": "66",
        },
    )

    frame = _records_to_prices([record])

    assert frame.loc[0, "timestamp_ms"] == 60_000
    assert frame.loc[0, "price"] == 1.1
    assert frame.loc[0, "quote_volume"] == 110.0
    assert frame.loc[0, "trade_count"] == 25
    assert frame.loc[0, "taker_buy_base"] == 60.0
    assert frame.loc[0, "taker_buy_quote"] == 66.0
