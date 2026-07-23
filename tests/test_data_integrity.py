from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from xasp.data_integrity import MINUTE_MS, audit_price_store


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


def test_contiguous_partition_has_hash_and_passes(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    path = root / "2025-01.parquet"
    _frame([0, MINUTE_MS, 2 * MINUTE_MS]).to_parquet(path, index=False)

    report = audit_price_store(root)

    assert report.status == "PASS"
    assert report.coverage_ratio == 1.0
    assert report.missing_minutes == 0
    assert report.structural_valid is True
    assert report.dataset_fingerprint_sha256 is not None
    assert len(report.dataset_fingerprint_sha256) == 64
    assert len(report.partitions[0].sha256) == 64


def test_missing_minute_is_reported_without_fabrication(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    _frame([0, 2 * MINUTE_MS]).to_parquet(root / "2025-01.parquet", index=False)

    report = audit_price_store(root, minimum_coverage_ratio=1.0)

    assert report.status == "WARN"
    assert report.reason == "minute_coverage_below_threshold"
    assert report.expected_rows == 3
    assert report.unique_rows == 2
    assert report.missing_minutes == 1


def test_invalid_ohlc_fails_structural_integrity(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    frame = _frame([0])
    frame.loc[0, "high"] = 0.95
    frame.to_parquet(root / "2025-01.parquet", index=False)

    report = audit_price_store(root)

    assert report.status == "FAIL"
    assert report.reason == "structural_price_integrity_failed"
    assert report.structural_valid is False
    assert report.partitions[0].invalid_ohlc_rows == 1


def test_dataset_fingerprint_changes_when_observed_data_changes(tmp_path: Path) -> None:
    root = tmp_path / "prices"
    root.mkdir()
    path = root / "2025-01.parquet"
    frame = _frame([0, MINUTE_MS])
    frame.to_parquet(path, index=False)
    first = audit_price_store(root)
    frame.loc[1, "volume"] = 101.0
    frame.to_parquet(path, index=False)

    second = audit_price_store(root)

    assert first.dataset_fingerprint_sha256 != second.dataset_fingerprint_sha256
    assert first.partitions[0].sha256 != second.partitions[0].sha256


def test_report_is_saved_atomically_and_no_data_waits(tmp_path: Path) -> None:
    report = audit_price_store(tmp_path / "missing")
    output = tmp_path / "reports" / "data_integrity.json"

    report.save(output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["status"] == "WAIT"
    assert payload["reason"] == "no_price_partitions"
    assert not output.with_suffix(".json.tmp").exists()
