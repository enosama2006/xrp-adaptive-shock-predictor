"""Restart-safe minute pipeline for observed Binance candles and OHLC targets."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from .anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore
from .data.binance import BinanceDataClient
from .dataset_state import DatasetStateStore
from .labeling import CandlePoint
from .partitioned_anchor_builder import (
    AnchorBuildResult,
    build_partitioned_anchor_dataset,
)
from .price_store import (
    CORE_PRICE_COLUMNS,
    OPTIONAL_PRICE_COLUMNS,
    PRICE_COLUMNS,
    PartitionedPriceStore,
    PriceStoreStats,
    normalize_price_frame,
)

MINUTE_MS = 60_000


class KlineRecord(Protocol):
    event_time_ms: int
    payload: dict[str, object]


class SpotKlineClient(Protocol):
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> Iterator[KlineRecord]: ...


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

    @property
    def price_partitions(self) -> Path:
        return self.prices.with_suffix("")


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
    price_partition_count: int = 0
    anchor_partition_count: int = 0


def _normalize_completed_minute_timestamp(timestamp_ms: int) -> int:
    """Normalize Binance ``closeTime`` (...59,999) to its availability boundary."""

    return timestamp_ms + 1 if timestamp_ms % MINUTE_MS == MINUTE_MS - 1 else timestamp_ms


def _load_prices(path: Path) -> pd.DataFrame:
    """Compatibility loader backed by the partition store and legacy migration."""

    return PartitionedPriceStore(path.with_suffix(""), legacy_path=path).load()


ScalarValue = str | bytes | bytearray | int | float


def _scalar_value(value: object, name: str) -> ScalarValue:
    if isinstance(value, (str, bytes, bytearray, int, float)):
        return value
    raise ValueError(f"kline field {name!r} has an unsupported scalar value")


def _required_float(payload: dict[str, object], name: str) -> float:
    if name not in payload:
        raise ValueError(f"kline payload missing required field: {name}")
    return float(_scalar_value(payload[name], name))


def _required_int(value: object, name: str) -> int:
    return int(_scalar_value(value, name))


def _optional_float(payload: dict[str, object], name: str) -> float | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return float(_scalar_value(value, name))


def _optional_int(payload: dict[str, object], name: str) -> int | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    return int(_scalar_value(value, name))


def _records_to_prices(records: Iterable[KlineRecord]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        payload = record.payload
        raw_close_time = _required_int(
            payload.get("close_time_ms", record.event_time_ms),
            "close_time_ms",
        )
        timestamp_ms = _normalize_completed_minute_timestamp(raw_close_time)
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


def _merge_prices(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Compatibility merge used by tests and migration utilities."""

    if existing.empty:
        return normalize_price_frame(incoming)
    if incoming.empty:
        return normalize_price_frame(existing)
    return normalize_price_frame(pd.concat([existing, incoming], ignore_index=True))


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
    """Checkpoint the missing tail into monthly partitions, then build OHLC labels."""

    def __init__(
        self,
        paths: PipelinePaths,
        config: PipelineConfig = PipelineConfig(),
        client: SpotKlineClient | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.client = client
        self.price_store = PartitionedPriceStore(
            paths.price_partitions,
            legacy_path=paths.prices,
        )
        self.last_anchor_build_result: AnchorBuildResult | None = None

    def _requested_start(self, stats: PriceStoreStats) -> int:
        if stats.max_timestamp_ms is None:
            return self.config.bootstrap_start_ms
        overlap = self.config.overlap_minutes * MINUTE_MS
        return max(
            self.config.bootstrap_start_ms,
            int(stats.max_timestamp_ms) - overlap + 1,
        )

    @staticmethod
    def _notify(
        callback: ProgressCallback | None,
        *,
        stage: str,
        start_ms: int,
        end_ms: int,
        expected_rows: int,
        processed_rows: int,
        stats: PriceStoreStats,
        checkpoint_writes: int,
    ) -> None:
        if callback is None:
            return
        callback(
            PipelineProgress(
                stage=stage,
                requested_start_ms=start_ms,
                requested_end_ms=end_ms,
                expected_rows=expected_rows,
                processed_rows=processed_rows,
                total_price_rows=stats.total_rows,
                checkpoint_writes=checkpoint_writes,
                current_watermark_ms=stats.max_timestamp_ms,
                progress_fraction=_progress_fraction(processed_rows, expected_rows),
            )
        )

    def _persist_checkpoint(
        self,
        buffered_records: list[KlineRecord],
        *,
        end_time_ms: int,
        state_store: DatasetStateStore,
    ) -> tuple[int, PriceStoreStats]:
        incoming = _records_to_prices(buffered_records)
        if not incoming.empty:
            # Binance may return the currently forming candle. Its normalized
            # availability timestamp lies after the request cutoff and must not
            # enter point-in-time features, labels, or predictions.
            incoming = incoming[incoming["timestamp_ms"] <= end_time_ms].copy()
        if incoming.empty:
            return 0, self.price_store.stats()

        stats = self.price_store.append(incoming)
        if stats.max_timestamp_ms is None:
            raise ValueError("price store did not expose a watermark after append")
        state = state_store.load()
        state.advance_raw_watermark(
            f"binance_spot:{self.config.symbol}:kline_1m",
            stats.max_timestamp_ms,
        )
        state_store.save(state)
        return int(len(incoming)), stats

    def _migrate_legacy_if_needed(
        self,
        *,
        end_time_ms: int,
        callback: ProgressCallback | None,
    ) -> PriceStoreStats:
        before = self.price_store.stats()
        if not self.price_store.needs_legacy_migration:
            self.price_store.ensure_ready()
            return self.price_store.stats()
        expected = before.total_rows
        self._notify(
            callback,
            stage="MIGRATE_PRICE_STORAGE",
            start_ms=before.min_timestamp_ms or self.config.bootstrap_start_ms,
            end_ms=end_time_ms,
            expected_rows=expected,
            processed_rows=0,
            stats=before,
            checkpoint_writes=0,
        )
        after = self.price_store.migrate_legacy()
        self._notify(
            callback,
            stage="MIGRATE_PRICE_STORAGE",
            start_ms=after.min_timestamp_ms or self.config.bootstrap_start_ms,
            end_ms=end_time_ms,
            expected_rows=expected,
            processed_rows=expected,
            stats=after,
            checkpoint_writes=after.partition_count,
        )
        return after

    def run(
        self,
        end_time_ms: int,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineRunResult:
        if end_time_ms < self.config.bootstrap_start_ms:
            raise ValueError("end_time_ms precedes bootstrap_start_ms")

        stats = self._migrate_legacy_if_needed(
            end_time_ms=end_time_ms,
            callback=progress_callback,
        )
        start_time_ms = self._requested_start(stats)
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
            stats=stats,
            checkpoint_writes=checkpoint_writes,
        )

        owns_client = self.client is None
        client: SpotKlineClient = self.client or BinanceDataClient()
        buffer: list[KlineRecord] = []
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
                accepted, stats = self._persist_checkpoint(
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
                    stats=stats,
                    checkpoint_writes=checkpoint_writes,
                )

            if buffer:
                accepted, stats = self._persist_checkpoint(
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
                    stats=stats,
                    checkpoint_writes=checkpoint_writes,
                )
        finally:
            if owns_client and isinstance(client, BinanceDataClient):
                client.close()

        stats = self.price_store.stats()
        self._notify(
            progress_callback,
            stage="BUILD_ANCHORS",
            start_ms=start_time_ms,
            end_ms=end_time_ms,
            expected_rows=expected_rows,
            processed_rows=max(processed_rows, expected_rows),
            stats=stats,
            checkpoint_writes=checkpoint_writes,
        )
        prices = self.price_store.load()
        anchor_build = build_partitioned_anchor_dataset(
            prices,
            AnchorDatasetStore(self.paths.anchors),
            state_store,
            self.config.anchor_config,
        )
        self.last_anchor_build_result = anchor_build
        state = state_store.load()

        self._notify(
            progress_callback,
            stage="DATA_CHECKPOINTED",
            start_ms=start_time_ms,
            end_ms=end_time_ms,
            expected_rows=expected_rows,
            processed_rows=max(processed_rows, expected_rows),
            stats=stats,
            checkpoint_writes=checkpoint_writes,
        )
        return PipelineRunResult(
            requested_start_ms=start_time_ms,
            requested_end_ms=end_time_ms,
            fetched_rows=processed_rows,
            total_price_rows=stats.total_rows,
            anchor_rows=anchor_build.stats.total_rows,
            pending_labels=state.pending_label_count,
            finalized_labels=state.finalized_label_count,
            checkpoint_writes=checkpoint_writes,
            price_partition_count=stats.partition_count,
            anchor_partition_count=anchor_build.stats.partition_count,
        )


__all__ = [
    "CORE_PRICE_COLUMNS",
    "OPTIONAL_PRICE_COLUMNS",
    "PRICE_COLUMNS",
    "IncrementalResearchPipeline",
    "PipelineConfig",
    "PipelinePaths",
    "PipelineProgress",
    "PipelineRunResult",
]
