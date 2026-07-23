from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from xasp.price_store import PartitionedPriceStore


def _timestamp(year: int, month: int, day: int, minute: int = 0) -> int:
    value = datetime(year, month, day, tzinfo=UTC).timestamp() * 1000
    return int(value) + minute * 60_000


def _frame(timestamps: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp_ms": timestamps,
            "price": [1.0 + index / 100 for index in range(len(timestamps))],
            "open": [1.0] * len(timestamps),
            "high": [1.1] * len(timestamps),
            "low": [0.9] * len(timestamps),
            "volume": [100.0] * len(timestamps),
        }
    )


def test_price_store_writes_only_affected_utc_months(tmp_path: Path) -> None:
    legacy = tmp_path / "prices.parquet"
    store = PartitionedPriceStore(tmp_path / "prices", legacy_path=legacy)
    january = _timestamp(2025, 1, 31, 1)
    february = _timestamp(2025, 2, 1, 1)

    stats = store.append(_frame([january, february]))

    assert stats.total_rows == 2
    assert stats.partition_count == 2
    assert (tmp_path / "prices" / "2025-01.parquet").exists()
    assert (tmp_path / "prices" / "2025-02.parquet").exists()
    loaded = store.load()
    assert loaded["timestamp_ms"].tolist() == [january, february]


def test_price_store_deduplicates_overlap_inside_partition(tmp_path: Path) -> None:
    store = PartitionedPriceStore(tmp_path / "prices")
    first = _timestamp(2025, 3, 1, 0)
    second = _timestamp(2025, 3, 1, 1)
    store.append(_frame([first, second]))
    replacement = _frame([second])
    replacement.loc[0, "price"] = 2.0

    stats = store.append(replacement)

    assert stats.total_rows == 2
    loaded = store.load()
    assert float(loaded.loc[loaded["timestamp_ms"] == second, "price"].iloc[0]) == 2.0


def test_legacy_single_file_is_migrated_without_deletion(tmp_path: Path) -> None:
    legacy = tmp_path / "prices.parquet"
    timestamps = [_timestamp(2024, 12, 31), _timestamp(2025, 1, 1)]
    _frame(timestamps).to_parquet(legacy, index=False)
    store = PartitionedPriceStore(tmp_path / "prices", legacy_path=legacy)

    stats = store.migrate_legacy()

    assert legacy.exists()
    assert stats.migrated_from_legacy is True
    assert stats.partition_count == 2
    assert store.load()["timestamp_ms"].tolist() == timestamps


def test_range_load_skips_unrelated_months(tmp_path: Path) -> None:
    store = PartitionedPriceStore(tmp_path / "prices")
    january = _timestamp(2025, 1, 1)
    february = _timestamp(2025, 2, 1)
    march = _timestamp(2025, 3, 1)
    store.append(_frame([january, february, march]))

    loaded = store.load(start_ms=february, end_ms=february)

    assert loaded["timestamp_ms"].tolist() == [february]
