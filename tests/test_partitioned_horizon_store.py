from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from xasp.partitioned_horizon_store import (
    HorizonPartitionKey,
    PartitionedHorizonStore,
)

COLUMNS = ("anchor_timestamp_ms", "horizon_minutes", "value", "status")


def _timestamp(year: int, month: int, day: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000) + minute * 60_000


def _store(tmp_path: Path) -> PartitionedHorizonStore:
    return PartitionedHorizonStore(
        root=tmp_path / "derived",
        legacy_path=tmp_path / "derived.parquet",
        columns=COLUMNS,
        key_columns=("anchor_timestamp_ms", "horizon_minutes"),
        timestamp_column="anchor_timestamp_ms",
        horizon_column="horizon_minutes",
        dataset_name="test_derived",
        status_column="status",
    )


def test_upsert_partitions_by_month_and_horizon(tmp_path: Path) -> None:
    store = _store(tmp_path)
    january = _timestamp(2025, 1, 31, 1)
    february = _timestamp(2025, 2, 1, 1)
    frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": [january, january, february, february],
            "horizon_minutes": [15, 60, 15, 60],
            "value": [1.0, 2.0, 3.0, 4.0],
            "status": ["FINAL", "FINAL", "PENDING", "PENDING"],
        }
    )

    stats = store.upsert(frame)

    assert stats.total_rows == 4
    assert stats.partition_count == 4
    assert stats.horizon_rows == {15: 2, 60: 2}
    assert stats.status_counts == {"FINAL": 2, "PENDING": 2}
    january_key = HorizonPartitionKey(15, "2025-01")
    assert store.has_partition(january_key)
    assert store.partition_rows(january_key) == 1
    assert store.has_partition(HorizonPartitionKey(60, "2025-02"))


def test_upsert_replaces_duplicate_key_inside_only_affected_partition(tmp_path: Path) -> None:
    store = _store(tmp_path)
    timestamp = _timestamp(2025, 3, 1)
    store.upsert(
        pd.DataFrame(
            {
                "anchor_timestamp_ms": [timestamp],
                "horizon_minutes": [15],
                "value": [1.0],
                "status": ["PENDING"],
            }
        )
    )

    store.upsert(
        pd.DataFrame(
            {
                "anchor_timestamp_ms": [timestamp],
                "horizon_minutes": [15],
                "value": [2.0],
                "status": ["FINAL"],
            }
        )
    )

    loaded = store.load_partition(HorizonPartitionKey(15, "2025-03"))
    assert len(loaded) == 1
    assert float(loaded.iloc[0]["value"]) == 2.0
    assert loaded.iloc[0]["status"] == "FINAL"


def test_load_filters_horizon_time_and_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _timestamp(2025, 4, 1)
    second = _timestamp(2025, 4, 1, 1)
    store.upsert(
        pd.DataFrame(
            {
                "anchor_timestamp_ms": [first, second, first],
                "horizon_minutes": [15, 15, 60],
                "value": [1.0, 2.0, 3.0],
                "status": ["FINAL", "PENDING", "FINAL"],
            }
        )
    )

    loaded = store.load(
        horizons=(15,),
        start_ms=first,
        end_ms=second,
        statuses=("FINAL",),
    )

    assert loaded[["anchor_timestamp_ms", "horizon_minutes"]].values.tolist() == [[first, 15]]


def test_legacy_file_migrates_without_deletion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    timestamp = _timestamp(2024, 12, 31)
    pd.DataFrame(
        {
            "anchor_timestamp_ms": [timestamp],
            "horizon_minutes": [60],
            "value": [5.0],
            "status": ["FINAL"],
        }
    ).to_parquet(store.legacy_path, index=False)

    stats = store.migrate_legacy()

    assert store.legacy_path is not None and store.legacy_path.exists()
    assert stats.migrated_from_legacy is True
    assert stats.total_rows == 1
    assert store.load()["anchor_timestamp_ms"].tolist() == [timestamp]
