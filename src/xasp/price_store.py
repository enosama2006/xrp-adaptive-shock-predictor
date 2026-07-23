"""Monthly partitioned storage for observed completed one-minute candles.

The legacy platform rewrote one growing Parquet file on every checkpoint. That
is acceptable for a short bootstrap but not for several years of minute data.
This store writes only the affected UTC month partitions, keeps an atomic
manifest, and can migrate the existing single-file dataset without deleting it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

CORE_PRICE_COLUMNS = ["timestamp_ms", "price", "open", "high", "low", "volume"]
OPTIONAL_PRICE_COLUMNS = [
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
]
PRICE_COLUMNS = [*CORE_PRICE_COLUMNS, *OPTIONAL_PRICE_COLUMNS]
PRICE_STORE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PriceStoreStats:
    total_rows: int
    partition_count: int
    min_timestamp_ms: int | None
    max_timestamp_ms: int | None
    migrated_from_legacy: bool


@dataclass(frozen=True, slots=True)
class PricePartitionInfo:
    key: str
    path: str
    rows: int
    min_timestamp_ms: int
    max_timestamp_ms: int


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the governed schema with unique monotonically increasing minutes."""

    if frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    missing_core = set(CORE_PRICE_COLUMNS) - set(frame.columns)
    if missing_core:
        raise ValueError(f"price dataset missing core columns: {sorted(missing_core)}")
    normalized = frame.copy()
    for column in OPTIONAL_PRICE_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    normalized = normalized[PRICE_COLUMNS]
    normalized["timestamp_ms"] = normalized["timestamp_ms"].astype("int64")
    normalized = normalized.drop_duplicates("timestamp_ms", keep="last")
    normalized = normalized.sort_values("timestamp_ms", ignore_index=True)
    if not normalized["timestamp_ms"].is_monotonic_increasing:
        raise ValueError("price timestamps must be monotonic")
    return normalized.reindex(columns=PRICE_COLUMNS)


def _month_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m")


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


class PartitionedPriceStore:
    """Atomic UTC-month partitions with safe migration from ``prices.parquet``."""

    def __init__(self, root: Path, legacy_path: Path | None = None) -> None:
        self.root = root
        self.legacy_path = legacy_path
        self.manifest_path = root / "manifest.json"

    def _partition_path(self, key: str) -> Path:
        return self.root / f"{key}.parquet"

    def _partition_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("????-??.parquet"))

    @property
    def needs_legacy_migration(self) -> bool:
        return bool(
            self.legacy_path is not None
            and self.legacy_path.exists()
            and not self._partition_paths()
        )

    @property
    def exists(self) -> bool:
        return bool(
            self._partition_paths()
            or (self.legacy_path is not None and self.legacy_path.exists())
        )

    def _read_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("price-store manifest must be a JSON object")
        if int(payload.get("schema_version", -1)) != PRICE_STORE_SCHEMA_VERSION:
            raise ValueError("unsupported price-store manifest schema")
        return payload

    def _rebuild_manifest(self, *, migrated_from_legacy: bool | None = None) -> dict[str, Any]:
        previous = self._read_manifest()
        migrated = (
            bool(previous.get("migrated_from_legacy", False))
            if previous is not None and migrated_from_legacy is None
            else bool(migrated_from_legacy)
        )
        partitions: list[PricePartitionInfo] = []
        for path in self._partition_paths():
            timestamps = pd.read_parquet(path, columns=["timestamp_ms"])
            if timestamps.empty:
                continue
            values = timestamps["timestamp_ms"].astype("int64")
            partitions.append(
                PricePartitionInfo(
                    key=path.stem,
                    path=path.name,
                    rows=int(len(values)),
                    min_timestamp_ms=int(values.min()),
                    max_timestamp_ms=int(values.max()),
                )
            )
        total_rows = sum(item.rows for item in partitions)
        min_timestamp = (
            None if not partitions else min(item.min_timestamp_ms for item in partitions)
        )
        max_timestamp = (
            None if not partitions else max(item.max_timestamp_ms for item in partitions)
        )
        payload: dict[str, Any] = {
            "schema_version": PRICE_STORE_SCHEMA_VERSION,
            "granularity": "UTC_MONTH",
            "columns": PRICE_COLUMNS,
            "total_rows": total_rows,
            "partition_count": len(partitions),
            "min_timestamp_ms": min_timestamp,
            "max_timestamp_ms": max_timestamp,
            "migrated_from_legacy": migrated,
            "updated_at": datetime.now(UTC).isoformat(),
            "partitions": [asdict(item) for item in partitions],
        }
        _atomic_write_json(payload, self.manifest_path)
        return payload

    def ensure_ready(self) -> None:
        if self.needs_legacy_migration:
            self.migrate_legacy()
        elif self._partition_paths() and not self.manifest_path.exists():
            self._rebuild_manifest()

    def migrate_legacy(self) -> PriceStoreStats:
        """Copy the legacy file into partitions; never delete the original file."""

        if not self.needs_legacy_migration:
            return self.stats()
        assert self.legacy_path is not None
        legacy = normalize_price_frame(pd.read_parquet(self.legacy_path))
        self.append(legacy, migrated_from_legacy=True)
        return self.stats()

    def append(
        self,
        incoming: pd.DataFrame,
        *,
        migrated_from_legacy: bool | None = None,
    ) -> PriceStoreStats:
        normalized = normalize_price_frame(incoming)
        if normalized.empty:
            self.ensure_ready()
            return self.stats()

        self.root.mkdir(parents=True, exist_ok=True)
        month_keys = normalized["timestamp_ms"].map(lambda value: _month_key(int(value)))
        for key, group in normalized.groupby(month_keys, sort=True):
            path = self._partition_path(str(key))
            if path.exists():
                existing = normalize_price_frame(pd.read_parquet(path))
                merged = normalize_price_frame(pd.concat([existing, group], ignore_index=True))
            else:
                merged = normalize_price_frame(group)
            if not merged.empty and any(
                _month_key(int(value)) != str(key) for value in merged["timestamp_ms"]
            ):
                raise ValueError(f"price partition contains timestamps outside UTC month {key}")
            _atomic_write_parquet(merged, path)

        self._rebuild_manifest(migrated_from_legacy=migrated_from_legacy)
        return self.stats()

    def _selected_partition_paths(
        self,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[Path]:
        manifest = self._read_manifest()
        if manifest is None:
            return self._partition_paths()
        selected: list[Path] = []
        raw_partitions = manifest.get("partitions", [])
        if not isinstance(raw_partitions, list):
            raise ValueError("price-store manifest partitions must be a list")
        for item in raw_partitions:
            if not isinstance(item, dict):
                raise ValueError("price-store partition manifest entry must be an object")
            minimum = int(item["min_timestamp_ms"])
            maximum = int(item["max_timestamp_ms"])
            if start_ms is not None and maximum < start_ms:
                continue
            if end_ms is not None and minimum > end_ms:
                continue
            selected.append(self.root / str(item["path"]))
        return selected

    def load(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if end_ms is not None and end_ms < 0:
            raise ValueError("end_ms must be non-negative")
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")

        self.ensure_ready()
        frames = [pd.read_parquet(path) for path in self._selected_partition_paths(start_ms, end_ms)]
        if not frames:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        combined = normalize_price_frame(pd.concat(frames, ignore_index=True))
        if start_ms is not None:
            combined = combined[combined["timestamp_ms"] >= start_ms]
        if end_ms is not None:
            combined = combined[combined["timestamp_ms"] <= end_ms]
        return combined.reset_index(drop=True).reindex(columns=PRICE_COLUMNS)

    def stats(self) -> PriceStoreStats:
        if self.needs_legacy_migration:
            assert self.legacy_path is not None
            legacy = normalize_price_frame(pd.read_parquet(self.legacy_path))
            return PriceStoreStats(
                total_rows=int(len(legacy)),
                partition_count=0,
                min_timestamp_ms=(
                    None if legacy.empty else int(legacy["timestamp_ms"].min())
                ),
                max_timestamp_ms=(
                    None if legacy.empty else int(legacy["timestamp_ms"].max())
                ),
                migrated_from_legacy=False,
            )
        manifest = self._read_manifest()
        if manifest is None:
            if self._partition_paths():
                manifest = self._rebuild_manifest()
            else:
                return PriceStoreStats(0, 0, None, None, False)
        return PriceStoreStats(
            total_rows=int(manifest.get("total_rows", 0)),
            partition_count=int(manifest.get("partition_count", 0)),
            min_timestamp_ms=(
                None
                if manifest.get("min_timestamp_ms") is None
                else int(manifest["min_timestamp_ms"])
            ),
            max_timestamp_ms=(
                None
                if manifest.get("max_timestamp_ms") is None
                else int(manifest["max_timestamp_ms"])
            ),
            migrated_from_legacy=bool(manifest.get("migrated_from_legacy", False)),
        )


__all__ = [
    "CORE_PRICE_COLUMNS",
    "OPTIONAL_PRICE_COLUMNS",
    "PRICE_COLUMNS",
    "PRICE_STORE_SCHEMA_VERSION",
    "PartitionedPriceStore",
    "PriceStoreStats",
    "normalize_price_frame",
]
