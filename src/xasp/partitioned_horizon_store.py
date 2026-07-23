"""Atomic monthly/horizon Parquet storage for large derived datasets.

Each partition contains one governed horizon and one UTC anchor month. The
store preserves any legacy single-file Parquet dataset during migration and
rewrites only partitions touched by an upsert. No missing rows are fabricated.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd

STORE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class HorizonPartitionKey:
    horizon_minutes: int
    month: str

    @property
    def identifier(self) -> str:
        return f"{self.horizon_minutes}:{self.month}"


@dataclass(frozen=True, slots=True)
class HorizonPartitionInfo:
    horizon_minutes: int
    month: str
    path: str
    rows: int
    min_timestamp_ms: int
    max_timestamp_ms: int
    status_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class HorizonStoreStats:
    total_rows: int
    partition_count: int
    min_timestamp_ms: int | None
    max_timestamp_ms: int | None
    horizon_rows: dict[int, int]
    status_counts: dict[str, int]
    migrated_from_legacy: bool

    @property
    def final_rows(self) -> int:
        return int(self.status_counts.get("FINAL", 0))

    @property
    def pending_rows(self) -> int:
        return int(self.status_counts.get("PENDING", 0))


class PartitionedHorizonStore:
    """Restart-safe derived-data store partitioned by horizon and UTC month."""

    def __init__(
        self,
        *,
        root: Path,
        legacy_path: Path | None,
        columns: tuple[str, ...],
        key_columns: tuple[str, ...],
        timestamp_column: str,
        horizon_column: str,
        dataset_name: str,
        status_column: str | None = None,
    ) -> None:
        if timestamp_column not in columns:
            raise ValueError("timestamp_column must be part of columns")
        if horizon_column not in columns:
            raise ValueError("horizon_column must be part of columns")
        if any(column not in columns for column in key_columns):
            raise ValueError("all key_columns must be part of columns")
        if status_column is not None and status_column not in columns:
            raise ValueError("status_column must be part of columns")
        self.root = root
        self.legacy_path = legacy_path
        self.columns = columns
        self.key_columns = key_columns
        self.timestamp_column = timestamp_column
        self.horizon_column = horizon_column
        self.dataset_name = dataset_name
        self.status_column = status_column
        self.manifest_path = root / "manifest.json"

    @staticmethod
    def _month_key(timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m")

    def _partition_path(self, key: HorizonPartitionKey) -> Path:
        return self.root / f"horizon={key.horizon_minutes:04d}" / f"{key.month}.parquet"

    def _partition_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("horizon=????/????-??.parquet"))

    @property
    def exists(self) -> bool:
        return bool(
            self._partition_paths() or (self.legacy_path is not None and self.legacy_path.exists())
        )

    @property
    def needs_legacy_migration(self) -> bool:
        return bool(
            self.legacy_path is not None
            and self.legacy_path.exists()
            and not self._partition_paths()
        )

    def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=list(self.columns))
        missing = set(self.columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{self.dataset_name} dataset missing columns: {sorted(missing)}")
        normalized = frame.loc[:, list(self.columns)].copy()
        normalized[self.timestamp_column] = normalized[self.timestamp_column].astype("int64")
        normalized[self.horizon_column] = normalized[self.horizon_column].astype("int64")
        normalized = normalized.drop_duplicates(list(self.key_columns), keep="last")
        normalized = normalized.sort_values(
            [self.timestamp_column, self.horizon_column],
            ignore_index=True,
        )
        return normalized.reindex(columns=list(self.columns))

    @staticmethod
    def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)

    @staticmethod
    def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{self.dataset_name} manifest must be a JSON object")
        if int(payload.get("schema_version", -1)) != STORE_SCHEMA_VERSION:
            raise ValueError(f"unsupported {self.dataset_name} manifest schema")
        return cast(dict[str, Any], payload)

    def _partition_info(self, path: Path) -> HorizonPartitionInfo | None:
        selected = [self.timestamp_column, self.horizon_column]
        if self.status_column is not None:
            selected.append(self.status_column)
        frame = pd.read_parquet(path, columns=selected)
        if frame.empty:
            return None
        horizon_values = frame[self.horizon_column].dropna().astype("int64").unique()
        if len(horizon_values) != 1:
            raise ValueError(f"mixed horizons found in partition: {path}")
        horizon = int(horizon_values[0])
        expected_directory = f"horizon={horizon:04d}"
        if path.parent.name != expected_directory:
            raise ValueError(f"partition horizon path mismatch: {path}")
        timestamps = frame[self.timestamp_column].astype("int64")
        month = path.stem
        if any(self._month_key(int(value)) != month for value in timestamps):
            raise ValueError(f"partition contains timestamps outside UTC month: {path}")
        status_counts: dict[str, int] = {}
        if self.status_column is not None:
            raw_counts = frame[self.status_column].fillna("NULL").astype(str).value_counts()
            status_counts = {str(key): int(value) for key, value in raw_counts.items()}
        return HorizonPartitionInfo(
            horizon_minutes=horizon,
            month=month,
            path=str(path.relative_to(self.root)),
            rows=int(len(frame)),
            min_timestamp_ms=int(timestamps.min()),
            max_timestamp_ms=int(timestamps.max()),
            status_counts=status_counts,
        )

    def rebuild_manifest(
        self,
        *,
        migrated_from_legacy: bool | None = None,
    ) -> dict[str, Any]:
        previous = self._read_manifest()
        migrated = (
            bool(previous.get("migrated_from_legacy", False))
            if previous is not None and migrated_from_legacy is None
            else bool(migrated_from_legacy)
        )
        partitions = [
            info
            for path in self._partition_paths()
            if (info := self._partition_info(path)) is not None
        ]
        horizon_rows: dict[int, int] = {}
        status_counts: dict[str, int] = {}
        for info in partitions:
            horizon_rows[info.horizon_minutes] = (
                horizon_rows.get(info.horizon_minutes, 0) + info.rows
            )
            for status, count in info.status_counts.items():
                status_counts[status] = status_counts.get(status, 0) + count
        payload: dict[str, Any] = {
            "schema_version": STORE_SCHEMA_VERSION,
            "dataset_name": self.dataset_name,
            "partitioning": [self.horizon_column, "UTC_MONTH"],
            "columns": list(self.columns),
            "key_columns": list(self.key_columns),
            "total_rows": sum(info.rows for info in partitions),
            "partition_count": len(partitions),
            "min_timestamp_ms": (
                None if not partitions else min(info.min_timestamp_ms for info in partitions)
            ),
            "max_timestamp_ms": (
                None if not partitions else max(info.max_timestamp_ms for info in partitions)
            ),
            "horizon_rows": {str(key): value for key, value in sorted(horizon_rows.items())},
            "status_counts": status_counts,
            "migrated_from_legacy": migrated,
            "updated_at": datetime.now(UTC).isoformat(),
            "partitions": [asdict(info) for info in partitions],
        }
        self._atomic_write_json(payload, self.manifest_path)
        return payload

    def ensure_ready(self) -> None:
        if self.needs_legacy_migration:
            self.migrate_legacy()
        elif self._partition_paths() and not self.manifest_path.exists():
            self.rebuild_manifest()

    def migrate_legacy(self) -> HorizonStoreStats:
        if not self.needs_legacy_migration:
            return self.stats()
        assert self.legacy_path is not None
        legacy = self.normalize(pd.read_parquet(self.legacy_path))
        self.upsert(legacy, migrated_from_legacy=True)
        return self.stats()

    def _keys_for_frame(self, frame: pd.DataFrame) -> pd.Series:
        months = frame[self.timestamp_column].map(lambda value: self._month_key(int(value)))
        horizons = frame[self.horizon_column].astype("int64")
        return horizons.astype(str) + ":" + months

    @staticmethod
    def _parse_identifier(identifier: str) -> HorizonPartitionKey:
        horizon, month = identifier.split(":", maxsplit=1)
        return HorizonPartitionKey(int(horizon), month)

    def _write_normalized(self, normalized: pd.DataFrame) -> None:
        identifiers = self._keys_for_frame(normalized)
        for identifier, group in normalized.groupby(identifiers, sort=True):
            key = self._parse_identifier(str(identifier))
            path = self._partition_path(key)
            if path.exists():
                existing = self.normalize(pd.read_parquet(path))
                merged = self.normalize(pd.concat([existing, group], ignore_index=True))
            else:
                merged = self.normalize(group)
            if any(int(value) != key.horizon_minutes for value in merged[self.horizon_column]):
                raise ValueError(f"mixed horizons in upsert partition: {key.identifier}")
            if any(
                self._month_key(int(value)) != key.month for value in merged[self.timestamp_column]
            ):
                raise ValueError(f"mixed months in upsert partition: {key.identifier}")
            self._atomic_write_parquet(merged, path)

    def upsert_frames(
        self,
        frames: Iterable[pd.DataFrame],
        *,
        migrated_from_legacy: bool | None = None,
    ) -> HorizonStoreStats:
        """Stream multiple frames and rebuild the manifest exactly once."""

        self.root.mkdir(parents=True, exist_ok=True)
        wrote_any = False
        for frame in frames:
            normalized = self.normalize(frame)
            if normalized.empty:
                continue
            self._write_normalized(normalized)
            wrote_any = True
        if not wrote_any:
            self.ensure_ready()
            return self.stats()
        self.rebuild_manifest(migrated_from_legacy=migrated_from_legacy)
        return self.stats()

    def upsert(
        self,
        incoming: pd.DataFrame,
        *,
        migrated_from_legacy: bool | None = None,
    ) -> HorizonStoreStats:
        return self.upsert_frames(
            (incoming,),
            migrated_from_legacy=migrated_from_legacy,
        )

    def replace(self, frame: pd.DataFrame) -> HorizonStoreStats:
        normalized = self.normalize(frame)
        if self.root.exists():
            shutil.rmtree(self.root)
        if normalized.empty:
            self.root.mkdir(parents=True, exist_ok=True)
            self.rebuild_manifest(migrated_from_legacy=False)
            return self.stats()
        return self.upsert(normalized, migrated_from_legacy=False)

    def partition_keys(self) -> tuple[HorizonPartitionKey, ...]:
        self.ensure_ready()
        manifest = self._read_manifest()
        if manifest is None:
            return ()
        raw = manifest.get("partitions", [])
        if not isinstance(raw, list):
            raise ValueError(f"{self.dataset_name} manifest partitions must be a list")
        keys = [
            HorizonPartitionKey(
                int(cast(dict[str, Any], item)["horizon_minutes"]),
                str(cast(dict[str, Any], item)["month"]),
            )
            for item in raw
            if isinstance(item, dict)
        ]
        return tuple(sorted(keys, key=lambda key: (key.month, key.horizon_minutes)))

    def has_partition(self, key: HorizonPartitionKey) -> bool:
        self.ensure_ready()
        return self._partition_path(key).exists()

    def partition_rows(self, key: HorizonPartitionKey) -> int:
        """Return row count from manifest metadata without reading Parquet data."""

        self.ensure_ready()
        manifest = self._read_manifest()
        if manifest is None:
            return 0
        raw = manifest.get("partitions", [])
        if not isinstance(raw, list):
            raise ValueError(f"{self.dataset_name} manifest partitions must be a list")
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("partition manifest entry must be an object")
            if (
                int(item["horizon_minutes"]) == key.horizon_minutes
                and str(item["month"]) == key.month
            ):
                return int(item["rows"])
        return 0

    def load_partition(self, key: HorizonPartitionKey) -> pd.DataFrame:
        self.ensure_ready()
        path = self._partition_path(key)
        if not path.exists():
            return pd.DataFrame(columns=list(self.columns))
        return self.normalize(pd.read_parquet(path))

    def _selected_partition_paths(
        self,
        *,
        horizons: tuple[int, ...] | None,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[Path]:
        manifest = self._read_manifest()
        if manifest is None:
            return []
        raw = manifest.get("partitions", [])
        if not isinstance(raw, list):
            raise ValueError(f"{self.dataset_name} manifest partitions must be a list")
        allowed = None if horizons is None else set(horizons)
        selected: list[Path] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("partition manifest entry must be an object")
            horizon = int(item["horizon_minutes"])
            minimum = int(item["min_timestamp_ms"])
            maximum = int(item["max_timestamp_ms"])
            if allowed is not None and horizon not in allowed:
                continue
            if start_ms is not None and maximum < start_ms:
                continue
            if end_ms is not None and minimum > end_ms:
                continue
            selected.append(self.root / str(item["path"]))
        return selected

    def load(
        self,
        *,
        horizons: tuple[int, ...] | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if end_ms is not None and end_ms < 0:
            raise ValueError("end_ms must be non-negative")
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        self.ensure_ready()
        frames = [
            pd.read_parquet(path)
            for path in self._selected_partition_paths(
                horizons=horizons,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        ]
        if not frames:
            return pd.DataFrame(columns=list(self.columns))
        combined = self.normalize(pd.concat(frames, ignore_index=True))
        if start_ms is not None:
            combined = combined[combined[self.timestamp_column] >= start_ms]
        if end_ms is not None:
            combined = combined[combined[self.timestamp_column] <= end_ms]
        if statuses is not None:
            if self.status_column is None:
                raise ValueError("statuses filter requested without status_column")
            combined = combined[combined[self.status_column].isin(statuses)]
        return combined.reset_index(drop=True).reindex(columns=list(self.columns))

    def stats(self) -> HorizonStoreStats:
        if self.needs_legacy_migration:
            assert self.legacy_path is not None
            legacy = self.normalize(pd.read_parquet(self.legacy_path))
            status_counts: dict[str, int] = {}
            if self.status_column is not None:
                raw = legacy[self.status_column].fillna("NULL").astype(str).value_counts()
                status_counts = {str(key): int(value) for key, value in raw.items()}
            horizon_counts = legacy.groupby(self.horizon_column).size().to_dict()
            return HorizonStoreStats(
                total_rows=int(len(legacy)),
                partition_count=0,
                min_timestamp_ms=(
                    None if legacy.empty else int(legacy[self.timestamp_column].min())
                ),
                max_timestamp_ms=(
                    None if legacy.empty else int(legacy[self.timestamp_column].max())
                ),
                horizon_rows={int(key): int(value) for key, value in horizon_counts.items()},
                status_counts=status_counts,
                migrated_from_legacy=False,
            )
        self.ensure_ready()
        manifest = self._read_manifest()
        if manifest is None:
            return HorizonStoreStats(0, 0, None, None, {}, {}, False)
        raw_horizon_rows = manifest.get("horizon_rows", {})
        raw_status_counts = manifest.get("status_counts", {})
        if not isinstance(raw_horizon_rows, dict) or not isinstance(raw_status_counts, dict):
            raise ValueError(f"invalid {self.dataset_name} manifest totals")
        return HorizonStoreStats(
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
            horizon_rows={int(key): int(value) for key, value in raw_horizon_rows.items()},
            status_counts={str(key): int(value) for key, value in raw_status_counts.items()},
            migrated_from_legacy=bool(manifest.get("migrated_from_legacy", False)),
        )


__all__ = [
    "HorizonPartitionInfo",
    "HorizonPartitionKey",
    "HorizonStoreStats",
    "PartitionedHorizonStore",
    "STORE_SCHEMA_VERSION",
]
