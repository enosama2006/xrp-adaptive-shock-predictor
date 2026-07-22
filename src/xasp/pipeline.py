"""Restart-safe minute pipeline for observed Binance candles and OHLC targets."""

from __future__ import annotations

from collections.abc import Callable
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
    checkpoint_rows: int = 10_000
    anchor_config: AnchorDatasetConfig = AnchorDatasetConfig()

    def __post_init__(self) -> None:
        if self.bootstrap_start_ms < 0:
            raise ValueError("bootstrap_start_ms must be non-negative")
        if self.overlap_minutes < 0:
            raise ValueError("overlap_minutes must be non-negative")
        if self.checkpoint_rows < 1:
            raise ValueError("checkpoint_rows must be positive")


@dataclass(frozen=True, slots=True)
class PipelinePaths:
    prices: Path
    anchors: Path
    state: Path


@dataclass(frozen=True, slots=True)
class PipelineProgress:
    stage: str
    requested_start_ms: int
    requested_end_ms: int
    expected_rows: int
    processed_rows: int
    total_price_rows: int
    checkpoint_writes: int
    current_watermark_ms: int | None
    progress_fraction: float


ProgressCallback = Callable[[PipelineProgress], None]


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    requested_start_ms: int
    requested_end_ms: int
    fetched_rows: int
    total_price_rows: int
    anchor_rows: int
    pending_labels: int
    finalized_labels: int
    checkpoint_writes: int = 0


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
    # Avoid pandas' deprecated dtype inference when the first real batch is
    # merged with the empty bootstrap frame.
    if existing.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = existing.copy()
    else:
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
    return [
        CandlePoint(
            timestamp_ms=int(row.timestamp_ms),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.price),
        )
        for row in frame.itertuples(index=False)
    ]


def _expected_minute_rows(start_time_ms: int, end_time_ms: int) -> int:
    if end_time_ms < start_time_ms:
        return 0
    return int((end_time_ms - start_time_ms) // MINUTE_MS) + 1


def _progress_fraction(processed_rows: int, expected_rows: int) -> float:
    if expected_rows <= 0:
        return 1.0
    return min(1.0, max(0.0, processed_rows / expected_rows))


class IncrementalResearchPipeline:
    """Checkpoint the missing tail, then build pending/final OHLC labels."""

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

    def _notify(
        self,
        callback: ProgressCallback | None,
        *,
        stage: str,
        start_ms: int,
        end_ms: int,
        expected_rows: int,
        processed_rows: int,
        prices: pd.DataFrame,
        checkpoint_writes: int,
    ) -> None:
        if callback is None:
            return
        watermark = None if prices.empty else int(prices["timestamp_ms"].max())
        callback(
            PipelineProgress(
                stage=stage,
                requested_start_ms=start_ms,
                requested_end_ms=end_ms,
                expected_rows=expected_rows,
                processed_rows=processed_rows,
                total_price_rows=len(prices),
                checkpoint_writes=checkpoint_writes,
                current_watermark_ms=watermark,
                progress_fraction=_progress_fraction(processed_rows, expected_rows),
            )
        )

    def _persist_checkpoint(
        self,
        existing: pd.DataFrame,
        buffered_records: list[object],
        *,
        end_time_ms: int,
        state_store: DatasetStateStore,
    ) -> tuple[pd.DataFrame, int]:
        incoming = _records_to_prices(buffered_records)
        if not incoming.empty:
            # Binance may return the currently forming candle. Its normalized
            # availability timestamp lies after the request cutoff and must not
            # enter point-in-time features, labels, or predictions.
            incoming = incoming[incoming["timestamp_ms"] <= end_time_ms].copy()
        if incoming.empty:
            return existing, 0

        merged = _merge_prices(existing, incoming)
        _atomic_write_parquet(merged, self.paths.prices)
        state = state_store.load()
        state.advance_raw_watermark(
            f"binance_spot:{self.config.symbol}:kline_1m",
            int(merged["timestamp_ms"].max()),
        )
        state_store.save(state)
        return merged, len(incoming)

    def run(
        self,
        end_time_ms: int,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineRunResult:
        if end_time_ms < self.config.bootstrap_start_ms:
            raise ValueError("end_time_ms precedes bootstrap_start_ms")

        existing = _load_prices(self.paths.prices)
        start_time_ms = self._requested_start(existing)
        expected_rows = _expected_minute_rows(start_time_ms, end_time_ms)
        processed_rows = 0
        checkpoint_writes = 0
        state_store = DatasetStateStore(self.paths.state)

        self._notify(
            progress_callback,
            stage="COLLECT_HISTORY",
            start_ms=start_time_ms,
            end_ms=end_time_ms,
            expected_rows=expected_rows,
            processed_rows=processed_rows,
            prices=existing,
            checkpoint_writes=checkpoint_writes,
        )

        owns_client = self.client is None
        client: SpotKlineClient = self.client or BinanceDataClient()
        buffer: list[object] = []
        try:
            for record in client.iter_spot_klines(
                symbol=self.config.symbol,
                interval="1m",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            ):
                buffer.append(record)
                if len(buffer) < self.config.checkpoint_rows:
                    continue
                existing, accepted = self._persist_checkpoint(
                    existing,
                    buffer,
                    end_time_ms=end_time_ms,
                    state_store=state_store,
                )
                buffer.clear()
                processed_rows += accepted
                if accepted:
                    checkpoint_writes += 1
                self._notify(
                    progress_callback,
                    stage="COLLECT_HISTORY",
                    start_ms=start_time_ms,
                    end_ms=end_time_ms,
                    expected_rows=expected_rows,
                    processed_rows=processed_rows,
                    prices=existing,
                    checkpoint_writes=checkpoint_writes,
                )

            if buffer:
                existing, accepted = self._persist_checkpoint(
                    existing,
                    buffer,
                    end_time_ms=end_time_ms,
                    state_store=state_store,
                )
                processed_rows += accepted
                if accepted:
                    checkpoint_writes += 1
                self._notify(
                    progress_callback,
                    stage="COLLECT_HISTORY",
                    start_ms=start_time_ms,
                    end_ms=end_time_ms,
                    expected_rows=expected_rows,
                    processed_rows=processed_rows,
                    prices=existing,
                    checkpoint_writes=checkpoint_writes,
                )
        finally:
            if owns_client and isinstance(client, BinanceDataClient):
                client.close()

        self._notify(
            progress_callback,
            stage="BUILD_ANCHORS",
            start_ms=start_time_ms,
            end_ms=end_time_ms,
            expected_rows=expected_rows,
            processed_rows=max(processed_rows, expected_rows),
            prices=existing,
            checkpoint_writes=checkpoint_writes,
        )
        anchors = update_anchor_dataset_from_candles(
            _to_candles(existing),
            AnchorDatasetStore(self.paths.anchors),
            state_store,
            self.config.anchor_config,
        )
        state = state_store.load()

        self._notify(
            progress_callback,
            stage="DATA_CHECKPOINTED",
            start_ms=start_time_ms,
            end_ms=end_time_ms,
            expected_rows=expected_rows,
            processed_rows=max(processed_rows, expected_rows),
            prices=existing,
            checkpoint_writes=checkpoint_writes,
        )
        return PipelineRunResult(
            requested_start_ms=start_time_ms,
            requested_end_ms=end_time_ms,
            fetched_rows=processed_rows,
            total_price_rows=len(existing),
            anchor_rows=len(anchors),
            pending_labels=state.pending_label_count,
            finalized_labels=state.finalized_label_count,
            checkpoint_writes=checkpoint_writes,
        )
