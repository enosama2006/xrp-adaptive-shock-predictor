"""Restart-safe end-to-end minute pipeline for observed Binance candles and anchors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from .anchor_dataset import (
    AnchorDatasetConfig,
    AnchorDatasetStore,
    update_anchor_dataset_from_candles,
)
from .data.binance import BinanceDataClient
from .dataset_state import DatasetStateStore
from .labeling import CandlePoint

MINUTE_MS = 60_000
CORE_PRICE_COLUMNS = ["timestamp_ms", "price", "open", "high", "low", "volume"]
OPTIONAL_PRICE_COLUMNS = [
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
]
PRICE_COLUMNS = [*CORE_PRICE_COLUMNS, *OPTIONAL_PRICE_COLUMNS]


class SpotKlineClient(Protocol):
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ): ...


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    symbol: str = "XRPUSDT"
    bootstrap_start_ms: int = 0
    overlap_minutes: int = 2
    anchor_config: AnchorDatasetConfig = AnchorDatasetConfig()

    def __post_init__(self) -> None:
        if self.bootstrap_start_ms < 0:
            raise ValueError("bootstrap_start_ms must be non-negative")
        if self.overlap_minutes < 0:
            raise ValueError("overlap_minutes must be non-negative")


@dataclass(frozen=True, slots=True)
class PipelinePaths:
    prices: Path
    anchors: Path
    state: Path


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    requested_start_ms: int
    requested_end_ms: int
    fetched_rows: int
    total_price_rows: int
    anchor_rows: int
    pending_labels: int
    finalized_labels: int


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _normalize_completed_minute_timestamp(timestamp_ms: int) -> int:
    """Normalize Binance ``closeTime`` (...59,999) to its availability boundary."""

    return timestamp_ms + 1 if timestamp_ms % MINUTE_MS == MINUTE_MS - 1 else timestamp_ms


def _load_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = pd.read_parquet(path)
    missing_core = set(CORE_PRICE_COLUMNS) - set(frame.columns)
    if missing_core:
        raise ValueError(f"price dataset missing core columns: {sorted(missing_core)}")
    for column in OPTIONAL_PRICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[PRICE_COLUMNS].copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].map(
        lambda value: _normalize_completed_minute_timestamp(int(value))
    )
    return (
        frame.drop_duplicates("timestamp_ms", keep="last")
        .sort_values("timestamp_ms", ignore_index=True)
        .reindex(columns=PRICE_COLUMNS)
    )


def _optional_float(payload: dict[str, object], name: str) -> float | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(payload: dict[str, object], name: str) -> int | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return int(value)


def _records_to_prices(records: list[object]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        payload: dict[str, object] = record.payload
        raw_close_time = int(payload.get("close_time_ms", record.event_time_ms))
        timestamp_ms = _normalize_completed_minute_timestamp(raw_close_time)
        rows.append(
            {
                "timestamp_ms": timestamp_ms,
                "price": float(payload["close"]),
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "volume": float(payload["volume"]),
                "quote_volume": _optional_float(payload, "quote_volume"),
                "trade_count": _optional_int(payload, "trade_count"),
                "taker_buy_base": _optional_float(payload, "taker_buy_base"),
                "taker_buy_quote": _optional_float(payload, "taker_buy_quote"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def _merge_prices(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([existing, incoming], ignore_index=True)
    if combined.empty:
        return combined.reindex(columns=PRICE_COLUMNS)
    combined["timestamp_ms"] = combined["timestamp_ms"].map(
        lambda value: _normalize_completed_minute_timestamp(int(value))
    )
    combined = combined.drop_duplicates("timestamp_ms", keep="last")
    combined = combined.sort_values("timestamp_ms", ignore_index=True)
    if not combined["timestamp_ms"].is_monotonic_increasing:
        raise ValueError("price timestamps must be monotonic")
    return combined.reindex(columns=PRICE_COLUMNS)


def _to_candles(frame: pd.DataFrame) -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    for row in frame.itertuples(index=False):
        candles.append(
            CandlePoint(
                timestamp_ms=int(row.timestamp_ms),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.price),
            )
        )
    return candles


class IncrementalResearchPipeline:
    """Backfill only the missing tail, then update pending/final OHLC labels."""

    def __init__(
        self,
        paths: PipelinePaths,
        config: PipelineConfig = PipelineConfig(),
        client: SpotKlineClient | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.client = client

    def _requested_start(self, prices: pd.DataFrame) -> int:
        if prices.empty:
            return self.config.bootstrap_start_ms
        latest = int(prices["timestamp_ms"].max())
        overlap = self.config.overlap_minutes * MINUTE_MS
        return max(self.config.bootstrap_start_ms, latest - overlap + 1)

    def run(self, end_time_ms: int) -> PipelineRunResult:
        if end_time_ms < self.config.bootstrap_start_ms:
            raise ValueError("end_time_ms precedes bootstrap_start_ms")

        existing = _load_prices(self.paths.prices)
        start_time_ms = self._requested_start(existing)
        owns_client = self.client is None
        client: SpotKlineClient = self.client or BinanceDataClient()
        try:
            records = list(
                client.iter_spot_klines(
                    symbol=self.config.symbol,
                    interval="1m",
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                )
            )
        finally:
            if owns_client and isinstance(client, BinanceDataClient):
                client.close()

        incoming = _records_to_prices(records)
        merged = _merge_prices(existing, incoming)
        if not merged.empty:
            _atomic_write_parquet(merged, self.paths.prices)

        anchors = update_anchor_dataset_from_candles(
            _to_candles(merged),
            AnchorDatasetStore(self.paths.anchors),
            DatasetStateStore(self.paths.state),
            self.config.anchor_config,
        )
        state = DatasetStateStore(self.paths.state).load()
        if not merged.empty:
            state.advance_raw_watermark(
                f"binance_spot:{self.config.symbol}:kline_1m",
                int(merged["timestamp_ms"].max()),
            )
            DatasetStateStore(self.paths.state).save(state)

        return PipelineRunResult(
            requested_start_ms=start_time_ms,
            requested_end_ms=end_time_ms,
            fetched_rows=len(incoming),
            total_price_rows=len(merged),
            anchor_rows=len(anchors),
            pending_labels=state.pending_label_count,
            finalized_labels=state.finalized_label_count,
        )
