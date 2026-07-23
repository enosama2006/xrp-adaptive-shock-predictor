"""Restart-safe expansion of observed history before the current local minimum.

The normal live pipeline resumes from the newest persisted candle. This module
handles the opposite direction: it fills an explicitly requested older range
without deleting current data, and persists an independent cursor so an
interruption cannot turn a partially filled range into a false completion.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from .data.binance import BinanceDataClient
from .price_store import PRICE_COLUMNS, PartitionedPriceStore, normalize_price_frame

MINUTE_MS = 60_000
STATE_SCHEMA_VERSION = 1


class KlineRecord(Protocol):
    event_time_ms: int
    payload: dict[str, object]


class HistoricalKlineClient(Protocol):
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> Iterator[KlineRecord]: ...


@dataclass(slots=True)
class HistoryExpansionState:
    schema_version: int
    symbol: str
    target_start_ms: int
    target_end_ms: int
    next_open_time_ms: int
    accepted_rows: int
    checkpoint_writes: int
    completed: bool
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        target_start_ms: int,
        target_end_ms: int,
    ) -> HistoryExpansionState:
        return cls(
            schema_version=STATE_SCHEMA_VERSION,
            symbol=symbol.upper(),
            target_start_ms=target_start_ms,
            target_end_ms=target_end_ms,
            next_open_time_ms=max(0, target_start_ms - MINUTE_MS),
            accepted_rows=0,
            checkpoint_writes=0,
            completed=False,
            updated_at=datetime.now(UTC).isoformat(),
        )

    def validate(self) -> None:
        if self.schema_version != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported history-expansion state schema")
        if not self.symbol:
            raise ValueError("history-expansion symbol is required")
        for value in (
            self.target_start_ms,
            self.target_end_ms,
            self.next_open_time_ms,
            self.accepted_rows,
            self.checkpoint_writes,
        ):
            if value < 0:
                raise ValueError("history-expansion state values must be non-negative")
        if self.target_end_ms < self.target_start_ms:
            raise ValueError("history-expansion target end precedes target start")


@dataclass(frozen=True, slots=True)
class HistoryExpansionResult:
    status: str
    reason: str
    symbol: str
    target_start_ms: int
    target_end_ms: int | None
    next_open_time_ms: int | None
    accepted_rows_this_run: int
    accepted_rows_total: int
    checkpoint_writes_this_run: int
    checkpoint_writes_total: int
    completed: bool
    current_min_timestamp_ms: int | None
    current_max_timestamp_ms: int | None
    price_partition_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HistoryExpansionStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> HistoryExpansionState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("history-expansion state must be a JSON object")
        state = HistoryExpansionState(**payload)
        state.validate()
        return state

    def save(self, state: HistoryExpansionState) -> None:
        state.validate()
        state.updated_at = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _ceil_minute(value: int) -> int:
    if value < 0:
        raise ValueError("target_start_ms must be non-negative")
    remainder = value % MINUTE_MS
    return value if remainder == 0 else value + MINUTE_MS - remainder


def _normalize_completed_timestamp(value: int) -> int:
    return value + 1 if value % MINUTE_MS == MINUTE_MS - 1 else value


ScalarValue = str | bytes | bytearray | int | float


def _scalar(value: object, name: str) -> ScalarValue:
    if isinstance(value, (str, bytes, bytearray, int, float)):
        return value
    raise ValueError(f"historical kline field {name!r} is not a scalar")


def _required_float(payload: dict[str, object], name: str) -> float:
    if name not in payload:
        raise ValueError(f"historical kline missing required field: {name}")
    return float(_scalar(payload[name], name))


def _optional_float(payload: dict[str, object], name: str) -> float | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return float(_scalar(value, name))


def _optional_int(payload: dict[str, object], name: str) -> int | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return int(_scalar(value, name))


def _records_to_prices(
    records: list[KlineRecord],
    *,
    target_start_ms: int,
    target_end_ms: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        payload = record.payload
        raw_close = int(_scalar(payload.get("close_time_ms", record.event_time_ms), "close_time_ms"))
        timestamp_ms = _normalize_completed_timestamp(raw_close)
        if timestamp_ms < target_start_ms or timestamp_ms > target_end_ms:
            continue
        rows.append(
            {
                "timestamp_ms": timestamp_ms,
                "price": _required_float(payload, "close"),
                "open": _required_float(payload, "open"),
                "high": _required_float(payload, "high"),
                "low": _required_float(payload, "low"),
                "volume": _required_float(payload, "volume"),
                "quote_volume": _optional_float(payload, "quote_volume"),
                "trade_count": _optional_int(payload, "trade_count"),
                "taker_buy_base": _optional_float(payload, "taker_buy_base"),
                "taker_buy_quote": _optional_float(payload, "taker_buy_quote"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return normalize_price_frame(pd.DataFrame(rows, columns=PRICE_COLUMNS))


def _result(
    *,
    status: str,
    reason: str,
    symbol: str,
    target_start_ms: int,
    target_end_ms: int | None,
    next_open_time_ms: int | None,
    accepted_rows_this_run: int,
    accepted_rows_total: int,
    checkpoint_writes_this_run: int,
    checkpoint_writes_total: int,
    completed: bool,
    store: PartitionedPriceStore,
) -> HistoryExpansionResult:
    stats = store.stats()
    return HistoryExpansionResult(
        status=status,
        reason=reason,
        symbol=symbol,
        target_start_ms=target_start_ms,
        target_end_ms=target_end_ms,
        next_open_time_ms=next_open_time_ms,
        accepted_rows_this_run=accepted_rows_this_run,
        accepted_rows_total=accepted_rows_total,
        checkpoint_writes_this_run=checkpoint_writes_this_run,
        checkpoint_writes_total=checkpoint_writes_total,
        completed=completed,
        current_min_timestamp_ms=stats.min_timestamp_ms,
        current_max_timestamp_ms=stats.max_timestamp_ms,
        price_partition_count=stats.partition_count,
    )


def expand_history(
    *,
    store: PartitionedPriceStore,
    state_store: HistoryExpansionStateStore,
    target_start_ms: int,
    symbol: str = "XRPUSDT",
    checkpoint_rows: int = 10_000,
    client: HistoricalKlineClient | None = None,
) -> HistoryExpansionResult:
    """Fill the older head of the observed timeline and persist every checkpoint."""

    if checkpoint_rows < 1:
        raise ValueError("checkpoint_rows must be positive")
    symbol = symbol.upper()
    target_start = _ceil_minute(target_start_ms)
    store.ensure_ready()
    stats = store.stats()
    saved_state = state_store.load()

    matching_incomplete = bool(
        saved_state is not None
        and not saved_state.completed
        and saved_state.symbol == symbol
        and saved_state.target_start_ms == target_start
    )
    if matching_incomplete:
        assert saved_state is not None
        state = saved_state
    else:
        if stats.min_timestamp_ms is None:
            return _result(
                status="WAIT",
                reason="no_existing_history_use_platform_bootstrap",
                symbol=symbol,
                target_start_ms=target_start,
                target_end_ms=None,
                next_open_time_ms=None,
                accepted_rows_this_run=0,
                accepted_rows_total=0,
                checkpoint_writes_this_run=0,
                checkpoint_writes_total=0,
                completed=False,
                store=store,
            )
        if stats.min_timestamp_ms <= target_start:
            return _result(
                status="READY",
                reason="requested_history_already_covered",
                symbol=symbol,
                target_start_ms=target_start,
                target_end_ms=stats.min_timestamp_ms,
                next_open_time_ms=None,
                accepted_rows_this_run=0,
                accepted_rows_total=0,
                checkpoint_writes_this_run=0,
                checkpoint_writes_total=0,
                completed=True,
                store=store,
            )
        target_end = int(stats.min_timestamp_ms) - MINUTE_MS
        state = HistoryExpansionState.create(
            symbol=symbol,
            target_start_ms=target_start,
            target_end_ms=target_end,
        )
        state_store.save(state)

    accepted_this_run = 0
    writes_this_run = 0
    last_accepted: int | None = None
    buffer: list[KlineRecord] = []
    owns_client = client is None
    market_client: HistoricalKlineClient = client or BinanceDataClient()

    def persist_buffer() -> None:
        nonlocal accepted_this_run, writes_this_run, last_accepted
        incoming = _records_to_prices(
            buffer,
            target_start_ms=state.target_start_ms,
            target_end_ms=state.target_end_ms,
        )
        buffer.clear()
        if incoming.empty:
            return
        store.append(incoming)
        accepted = int(len(incoming))
        latest = int(incoming["timestamp_ms"].max())
        accepted_this_run += accepted
        state.accepted_rows += accepted
        state.checkpoint_writes += 1
        writes_this_run += 1
        state.next_open_time_ms = latest
        last_accepted = latest
        if latest >= state.target_end_ms:
            state.completed = True
        state_store.save(state)
        print(
            "[XASP] Historical checkpoint: "
            f"rows={state.accepted_rows:,}, watermark={latest}, "
            f"target_end={state.target_end_ms}"
        )

    try:
        request_end_open_ms = max(0, state.target_end_ms - MINUTE_MS)
        for record in market_client.iter_spot_klines(
            symbol=symbol,
            interval="1m",
            start_time_ms=state.next_open_time_ms,
            end_time_ms=request_end_open_ms,
        ):
            buffer.append(record)
            if len(buffer) >= checkpoint_rows:
                persist_buffer()
            if state.completed:
                break
        if buffer and not state.completed:
            persist_buffer()
    finally:
        if owns_client and isinstance(market_client, BinanceDataClient):
            market_client.close()

    if state.completed or (
        last_accepted is not None and last_accepted >= state.target_end_ms
    ):
        state.completed = True
        state_store.save(state)
        return _result(
            status="READY",
            reason="older_history_expansion_completed",
            symbol=symbol,
            target_start_ms=state.target_start_ms,
            target_end_ms=state.target_end_ms,
            next_open_time_ms=state.next_open_time_ms,
            accepted_rows_this_run=accepted_this_run,
            accepted_rows_total=state.accepted_rows,
            checkpoint_writes_this_run=writes_this_run,
            checkpoint_writes_total=state.checkpoint_writes,
            completed=True,
            store=store,
        )

    return _result(
        status="WAIT",
        reason="historical_source_exhausted_before_target_end",
        symbol=symbol,
        target_start_ms=state.target_start_ms,
        target_end_ms=state.target_end_ms,
        next_open_time_ms=state.next_open_time_ms,
        accepted_rows_this_run=accepted_this_run,
        accepted_rows_total=state.accepted_rows,
        checkpoint_writes_this_run=writes_this_run,
        checkpoint_writes_total=state.checkpoint_writes,
        completed=False,
        store=store,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expand XASP observed history backwards")
    parser.add_argument("--target-start-ms", required=True, type=int)
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--root", type=Path, default=Path("data/prices"))
    parser.add_argument("--legacy", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("data/history_expansion_state.json"),
    )
    parser.add_argument("--checkpoint-rows", type=int, default=10_000)
    parser.add_argument("--fail-on-incomplete", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    result = expand_history(
        store=PartitionedPriceStore(args.root, legacy_path=args.legacy),
        state_store=HistoryExpansionStateStore(args.state),
        target_start_ms=args.target_start_ms,
        symbol=args.symbol,
        checkpoint_rows=args.checkpoint_rows,
    )
    payload = result.to_dict()
    payload["elapsed_seconds"] = round(time.time() - started, 3)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.fail_on_incomplete and result.reason not in {
        "older_history_expansion_completed",
        "requested_history_already_covered",
        "no_existing_history_use_platform_bootstrap",
    }:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
