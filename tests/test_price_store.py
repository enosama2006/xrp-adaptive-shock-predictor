from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from xasp.data_integrity import audit_price_store
from xasp.price_store import (
    LEGACY_PRICE_STORE_SCHEMA_VERSION,
    PRICE_STORE_SCHEMA_VERSION,
    PartitionedPriceStore,
)


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


def test_legacy_close_times_are_repaired_once_without_losing_raw_backup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data" / "prices"
    root.mkdir(parents=True)
    january_close = _timestamp(2025, 2, 1) - 1
    february_close = _timestamp(2025, 2, 1, 1) - 1
    _frame([january_close]).to_parquet(root / "2025-01.parquet", index=False)
    _frame([february_close]).to_parquet(root / "2025-02.parquet", index=False)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": LEGACY_PRICE_STORE_SCHEMA_VERSION,
                "migrated_from_legacy": True,
            }
        ),
        encoding="utf-8",
    )
    store = PartitionedPriceStore(root)

    store.ensure_ready()

    backup = root.with_name("prices.before-canonical-v2")
    assert backup.exists()
    assert (backup / "2025-01.parquet").exists()
    assert store.load()["timestamp_ms"].tolist() == [
        _timestamp(2025, 2, 1),
        _timestamp(2025, 2, 1, 1),
    ]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == PRICE_STORE_SCHEMA_VERSION
    assert manifest["migrated_from_legacy"] is True
    report = audit_price_store(root, minimum_coverage_ratio=1.0)
    assert report.status == "PASS"
    assert report.structural_valid is True

    store.ensure_ready()

    assert store.stats().total_rows == 2
    assert len(list(tmp_path.glob("data/prices.before-canonical-v2*"))) == 1


def test_second_precision_legacy_close_times_are_repaired(tmp_path: Path) -> None:
    root = tmp_path / "data" / "prices"
    root.mkdir(parents=True)
    month_boundary = _timestamp(2025, 2, 1)
    january_close = month_boundary - 1_000
    february_close = _timestamp(2025, 2, 1, 1) - 1_000
    _frame([january_close]).to_parquet(root / "2025-01.parquet", index=False)
    _frame([february_close]).to_parquet(root / "2025-02.parquet", index=False)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": LEGACY_PRICE_STORE_SCHEMA_VERSION}),
        encoding="utf-8",
    )
    store = PartitionedPriceStore(root)

    store.ensure_ready()

    assert store.load()["timestamp_ms"].tolist() == [
        month_boundary,
        _timestamp(2025, 2, 1, 1),
    ]
    backup = root.with_name("prices.before-canonical-v2")
    assert backup.exists()
    assert pd.read_parquet(backup / "2025-01.parquet")[
        "timestamp_ms"
    ].tolist() == [january_close]
    report = audit_price_store(root, minimum_coverage_ratio=1.0)
    assert report.status == "PASS"
    assert report.structural_valid is True


def test_unknown_timestamp_offsets_are_not_silently_repaired(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    unsupported = _timestamp(2025, 1, 1) + 123
    _frame([unsupported]).to_parquet(root / "2025-01.parquet", index=False)
    original = (root / "2025-01.parquet").read_bytes()
    store = PartitionedPriceStore(root)

    with pytest.raises(ValueError, match="neither a minute boundary nor a Binance closeTime"):
        store.ensure_ready()

    assert (root / "2025-01.parquet").read_bytes() == original
    assert not root.with_name("prices.before-canonical-v2").exists()


def test_ambiguous_legacy_month_is_rebuilt_authoritatively_and_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data" / "prices"
    root.mkdir(parents=True)
    ambiguous = 1_679_661_588_301
    authoritative = _timestamp(2023, 3, 24, 760)
    next_month_boundary = _timestamp(2023, 4, 1)
    _frame([ambiguous]).to_parquet(root / "2023-03.parquet", index=False)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": LEGACY_PRICE_STORE_SCHEMA_VERSION}),
        encoding="utf-8",
    )
    requested: list[str] = []

    def repair(month_key: str) -> pd.DataFrame:
        requested.append(month_key)
        return _frame([authoritative, next_month_boundary])

    store = PartitionedPriceStore(root)
    store.ensure_ready(partition_repair=repair)

    assert requested == ["2023-03"]
    assert store.load()["timestamp_ms"].tolist() == [
        authoritative,
        next_month_boundary,
    ]
    assert (root / "2023-04.parquet").exists()
    backup = root.with_name("prices.before-canonical-v2")
    assert backup.exists()
    assert pd.read_parquet(backup / "2023-03.parquet")[
        "timestamp_ms"
    ].tolist() == [ambiguous]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == PRICE_STORE_SCHEMA_VERSION


def test_invalid_authoritative_repair_does_not_modify_source(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    ambiguous = 1_679_661_588_301
    _frame([ambiguous]).to_parquet(root / "2023-03.parquet", index=False)
    original = (root / "2023-03.parquet").read_bytes()
    store = PartitionedPriceStore(root)

    with pytest.raises(ValueError, match="noncanonical timestamps"):
        store.ensure_ready(partition_repair=lambda _: _frame([ambiguous]))

    assert (root / "2023-03.parquet").read_bytes() == original
    assert not root.with_name("prices.before-canonical-v2").exists()
    assert not root.with_name("prices.canonical-v2.tmp").exists()


def test_interrupted_directory_swap_finishes_from_valid_staging(tmp_path: Path) -> None:
    root = tmp_path / "data" / "prices"
    staging = root.with_name("prices.canonical-v2.tmp")
    backup = root.with_name("prices.before-canonical-v2")
    timestamp = _timestamp(2025, 1, 1)
    PartitionedPriceStore(staging).append(_frame([timestamp]))
    backup.mkdir(parents=True)

    store = PartitionedPriceStore(root)
    store.ensure_ready()

    assert root.exists()
    assert not staging.exists()
    assert backup.exists()
    assert store.load()["timestamp_ms"].tolist() == [timestamp]
