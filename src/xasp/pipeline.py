"""Restart-safe end-to-end minute pipeline for raw prices and first-touch anchors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from .anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore, update_anchor_dataset
from .data.binance import BinanceDataClient
from .dataset_state import DatasetStateStore
from .labeling import PricePoint

MINUTE_MS = 60_000
PRICE_COLUMNS = ["timestamp_ms", "price", "open", "high", "low", "volume"]


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


def _load_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = pd.read_parquet(path)
    missing = set(PRICE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"price dataset missing columns: {sorted(missing)}")
    return frame[PRICE_COLUMNS].sort_values("timestamp_ms", ignore_index=True)


def _records_to_prices(records: list[object]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        payload = record.payload
        rows.append(
            {
                "timestamp_ms": int(record.event_time_ms),
                "price": float(payload["close"]),
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "volume": float(payload["volume"]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def _merge_prices(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([existing, incoming], ignore_index=True)
    if combined.empty:
        return combined.reindex(columns=PRICE_COLUMNS)
    combined = combined.drop_duplicates("timestamp_ms", keep="last")
    combined = combined.sort_values("timestamp_ms", ignore_index=True)
    if not combined["timestamp_ms"].is_monotonic_increasing:
        raise ValueError("price timestamps must be monotonic")
    return combined[PRICE_COLUMNS]


class IncrementalResearchPipeline:
    """Backfill only the missing tail, then update pending/final anchor labels."""

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

        points = [
            PricePoint(timestamp_ms=int(row.timestamp_ms), price=float(row.price))
            for row in merged.itertuples(index=False)
        ]
        anchors = update_anchor_dataset(
            points,
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
