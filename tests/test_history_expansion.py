from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xasp.history_expansion import (
    MINUTE_MS,
    HistoryExpansionStateStore,
    expand_history,
)
from xasp.price_store import PartitionedPriceStore


def _record(open_time_ms: int, price: float = 1.0) -> SimpleNamespace:
    close_time_ms = open_time_ms + MINUTE_MS - 1
    return SimpleNamespace(
        event_time_ms=close_time_ms,
        payload={
            "close_time_ms": close_time_ms,
            "open": str(price),
            "high": str(price + 0.01),
            "low": str(price - 0.01),
            "close": str(price),
            "volume": "100",
            "quote_volume": "100",
            "trade_count": 10,
            "taker_buy_base": "50",
            "taker_buy_quote": "50",
        },
    )


class RangeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ):
        del symbol, interval, limit
        self.calls.append((start_time_ms, end_time_ms))
        first = ((start_time_ms + MINUTE_MS - 1) // MINUTE_MS) * MINUTE_MS
        for open_time in range(first, end_time_ms + 1, MINUTE_MS):
            yield _record(open_time, 1.0 + open_time / MINUTE_MS / 1000)


class FailingClient(RangeClient):
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ):
        self.calls.append((start_time_ms, end_time_ms))
        del symbol, interval, limit
        first = ((start_time_ms + MINUTE_MS - 1) // MINUTE_MS) * MINUTE_MS
        for index, open_time in enumerate(
            range(first, end_time_ms + 1, MINUTE_MS),
            start=1,
        ):
            if index == 5:
                raise RuntimeError("simulated historical connection loss")
            yield _record(open_time)


class NoCallClient:
    def iter_spot_klines(self, **_: object):
        raise AssertionError("network should not be called")
        yield  # pragma: no cover


def _store(tmp_path: Path) -> PartitionedPriceStore:
    return PartitionedPriceStore(
        tmp_path / "data" / "prices",
        legacy_path=tmp_path / "data" / "prices.parquet",
    )


def _seed_existing(store: PartitionedPriceStore, timestamp_ms: int) -> None:
    import pandas as pd

    store.append(
        pd.DataFrame(
            {
                "timestamp_ms": [timestamp_ms],
                "price": [1.0],
                "open": [1.0],
                "high": [1.01],
                "low": [0.99],
                "volume": [100.0],
            }
        )
    )


def test_expansion_fills_the_older_head_and_keeps_existing_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_existing(store, 10 * MINUTE_MS)
    client = RangeClient()

    result = expand_history(
        store=store,
        state_store=HistoryExpansionStateStore(
            tmp_path / "data" / "history_expansion_state.json"
        ),
        target_start_ms=MINUTE_MS,
        checkpoint_rows=3,
        client=client,
    )

    assert result.status == "READY"
    assert result.reason == "older_history_expansion_completed"
    assert result.completed is True
    assert result.accepted_rows_total == 9
    assert store.load()["timestamp_ms"].tolist() == [
        minute * MINUTE_MS for minute in range(1, 11)
    ]
    assert client.calls == [(0, 8 * MINUTE_MS)]


def test_interruption_resumes_from_persisted_cursor_not_current_minimum(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_existing(store, 10 * MINUTE_MS)
    state_store = HistoryExpansionStateStore(
        tmp_path / "data" / "history_expansion_state.json"
    )

    with pytest.raises(RuntimeError, match="simulated historical connection loss"):
        expand_history(
            store=store,
            state_store=state_store,
            target_start_ms=MINUTE_MS,
            checkpoint_rows=3,
            client=FailingClient(),
        )

    interrupted_state = state_store.load()
    assert interrupted_state is not None
    assert interrupted_state.completed is False
    assert interrupted_state.accepted_rows == 3
    assert interrupted_state.next_open_time_ms == 3 * MINUTE_MS
    assert store.stats().min_timestamp_ms == MINUTE_MS

    resumed_client = RangeClient()
    result = expand_history(
        store=store,
        state_store=state_store,
        target_start_ms=MINUTE_MS,
        checkpoint_rows=2,
        client=resumed_client,
    )

    assert result.completed is True
    assert result.accepted_rows_total == 9
    assert resumed_client.calls == [(3 * MINUTE_MS, 8 * MINUTE_MS)]
    assert store.load()["timestamp_ms"].tolist() == [
        minute * MINUTE_MS for minute in range(1, 11)
    ]


def test_already_covered_history_makes_no_network_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_existing(store, MINUTE_MS)

    result = expand_history(
        store=store,
        state_store=HistoryExpansionStateStore(tmp_path / "state.json"),
        target_start_ms=MINUTE_MS,
        client=NoCallClient(),
    )

    assert result.status == "READY"
    assert result.reason == "requested_history_already_covered"
    assert result.accepted_rows_this_run == 0


def test_fresh_install_defers_to_normal_platform_bootstrap(tmp_path: Path) -> None:
    result = expand_history(
        store=_store(tmp_path),
        state_store=HistoryExpansionStateStore(tmp_path / "state.json"),
        target_start_ms=MINUTE_MS,
        client=NoCallClient(),
    )

    assert result.status == "WAIT"
    assert result.reason == "no_existing_history_use_platform_bootstrap"
    assert result.completed is False


def test_non_aligned_target_is_rounded_to_next_completed_minute(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_existing(store, 4 * MINUTE_MS)

    result = expand_history(
        store=store,
        state_store=HistoryExpansionStateStore(tmp_path / "state.json"),
        target_start_ms=MINUTE_MS + 1,
        checkpoint_rows=2,
        client=RangeClient(),
    )

    assert result.target_start_ms == 2 * MINUTE_MS
    assert store.stats().min_timestamp_ms == 2 * MINUTE_MS


def test_launchers_request_expansion_before_integrity_audit() -> None:
    expansion_launcher = Path("EXPAND_XASP_HISTORY_5Y.bat").read_text(encoding="utf-8")
    startup = Path("START_XASP.bat").read_text(encoding="utf-8")

    assert 'set "XASP_EXPAND_HISTORY=1"' in expansion_launcher
    assert "-m xasp.history_expansion" in startup
    assert startup.index("-m xasp.history_expansion") < startup.index("-m xasp.data_integrity")
