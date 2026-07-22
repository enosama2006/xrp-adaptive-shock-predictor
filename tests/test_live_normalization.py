from xasp.data.live import normalize_message


def test_agg_trade_normalization() -> None:
    row = normalize_message(
        {
            "data": {
                "e": "aggTrade",
                "s": "XRPUSDT",
                "T": 1000,
                "a": 7,
                "p": "1.25",
                "q": "10",
                "m": False,
            }
        },
        1010,
    )
    assert row is not None
    assert row.record_type == "agg_trade"
    assert row.source_sequence == 7
    assert row.payload["buyer_is_maker"] is False


def test_book_ticker_normalization() -> None:
    row = normalize_message(
        {
            "data": {
                "s": "BTCUSDT",
                "E": 1000,
                "u": 99,
                "b": "100",
                "B": "2",
                "a": "101",
                "A": "3",
            }
        },
        1005,
    )
    assert row is not None
    assert row.record_type == "book_ticker"
    assert row.payload["ask_price"] == "101"


def test_unknown_message_is_rejected() -> None:
    assert normalize_message({"data": {"s": "XRPUSDT", "e": "unknown"}}, 1) is None
