from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Venue = Literal["binance_spot", "binance_usdm"]
RecordType = Literal[
    "kline",
    "agg_trade",
    "book_ticker",
    "depth_snapshot",
    "depth_delta",
    "funding_rate",
    "open_interest",
    "liquidation",
]


class MarketRecord(BaseModel):
    """Canonical append-only market record.

    event_time_ms is the exchange event time. received_time_ms is local arrival time.
    payload contains source-specific fields without silently coercing unavailable values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: Venue
    symbol: str = Field(min_length=3)
    record_type: RecordType
    event_time_ms: int = Field(ge=0)
    received_time_ms: int = Field(ge=0)
    source_sequence: int | None = Field(default=None, ge=0)
    payload: dict[str, Any]

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()

    @property
    def latency_ms(self) -> int:
        return self.received_time_ms - self.event_time_ms


class DatasetFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relative_path: str
    sha256: str
    bytes: int = Field(ge=0)
    rows: int = Field(ge=0)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    dataset_id: str
    created_at_utc: datetime
    source: str
    symbols: list[str]
    start_event_time_ms: int | None
    end_event_time_ms: int | None
    files: list[DatasetFile]
    row_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    notes: list[str] = []


def utc_now() -> datetime:
    return datetime.now(UTC)


def file_digest(path: Path, *, rows: int) -> DatasetFile:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return DatasetFile(
        relative_path=path.name,
        sha256=digest.hexdigest(),
        bytes=path.stat().st_size,
        rows=rows,
    )
