"""Verifiable integrity audit for observed one-minute price partitions.

The audit is deliberately independent from model training. It verifies local
files, reports real gaps and malformed OHLC rows, and creates a deterministic
dataset fingerprint. It never fills missing candles or invents unavailable
market data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MINUTE_MS = 60_000
DEFAULT_MINIMUM_COVERAGE = 0.995
CORE_COLUMNS = ("timestamp_ms", "price", "open", "high", "low", "volume")


@dataclass(frozen=True, slots=True)
class PartitionAudit:
    key: str
    path: str
    rows: int
    unique_rows: int
    expected_rows: int
    missing_minutes: int
    duplicate_timestamps: int
    out_of_order_timestamps: int
    misaligned_timestamps: int
    invalid_ohlc_rows: int
    invalid_volume_rows: int
    min_timestamp_ms: int
    max_timestamp_ms: int
    coverage_ratio: float
    size_bytes: int
    sha256: str
    structural_valid: bool


@dataclass(frozen=True, slots=True)
class PriceIntegrityReport:
    status: str
    reason: str
    generated_at: str
    root: str
    source: str
    partition_count: int
    total_rows: int
    unique_rows: int
    expected_rows: int
    missing_minutes: int
    coverage_ratio: float
    minimum_coverage_ratio: float
    coverage_passed: bool
    structural_valid: bool
    min_timestamp_ms: int | None
    max_timestamp_ms: int | None
    dataset_fingerprint_sha256: str | None
    partitions: tuple[PartitionAudit, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _partition_key(path: Path) -> str:
    return path.stem if path.stem != "prices" else "legacy"


def audit_partition(path: Path) -> PartitionAudit:
    """Audit one observed Parquet file without normalizing away defects."""

    frame = pd.read_parquet(path)
    missing_columns = set(CORE_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"price partition {path.name} missing columns: {sorted(missing_columns)}"
        )
    if frame.empty:
        raise ValueError(f"price partition is empty: {path.name}")

    timestamps = _numeric(frame, "timestamp_ms")
    if timestamps.isna().any():
        raise ValueError(f"price partition has non-numeric timestamps: {path.name}")
    timestamp_values = timestamps.astype("int64")
    duplicate_timestamps = int(timestamp_values.duplicated(keep=False).sum())
    out_of_order = int((timestamp_values.diff().fillna(MINUTE_MS) < 0).sum())
    misaligned = int((timestamp_values % MINUTE_MS != 0).sum())
    unique_timestamps = timestamp_values.drop_duplicates().sort_values(ignore_index=True)
    minimum = int(unique_timestamps.min())
    maximum = int(unique_timestamps.max())
    expected_rows = int((maximum - minimum) // MINUTE_MS) + 1
    unique_rows = int(len(unique_timestamps))
    missing_minutes = max(0, expected_rows - unique_rows)
    coverage_ratio = unique_rows / expected_rows if expected_rows else 0.0

    open_price = _numeric(frame, "open")
    high = _numeric(frame, "high")
    low = _numeric(frame, "low")
    close = _numeric(frame, "price")
    volume = _numeric(frame, "volume")
    finite_ohlc = (
        np.isfinite(open_price)
        & np.isfinite(high)
        & np.isfinite(low)
        & np.isfinite(close)
    )
    invalid_ohlc = (
        ~finite_ohlc
        | (open_price <= 0)
        | (high <= 0)
        | (low <= 0)
        | (close <= 0)
        | (high < low)
        | (high < open_price)
        | (high < close)
        | (low > open_price)
        | (low > close)
    )
    invalid_ohlc_rows = int(invalid_ohlc.sum())
    invalid_volume_rows = int((~np.isfinite(volume) | (volume < 0)).sum())
    structural_valid = all(
        value == 0
        for value in (
            duplicate_timestamps,
            out_of_order,
            misaligned,
            invalid_ohlc_rows,
            invalid_volume_rows,
        )
    )
    return PartitionAudit(
        key=_partition_key(path),
        path=str(path),
        rows=int(len(frame)),
        unique_rows=unique_rows,
        expected_rows=expected_rows,
        missing_minutes=missing_minutes,
        duplicate_timestamps=duplicate_timestamps,
        out_of_order_timestamps=out_of_order,
        misaligned_timestamps=misaligned,
        invalid_ohlc_rows=invalid_ohlc_rows,
        invalid_volume_rows=invalid_volume_rows,
        min_timestamp_ms=minimum,
        max_timestamp_ms=maximum,
        coverage_ratio=coverage_ratio,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        structural_valid=structural_valid,
    )


def _fingerprint(partitions: list[PartitionAudit]) -> str:
    digest = hashlib.sha256()
    for item in partitions:
        digest.update(
            (
                f"{item.key}:{item.sha256}:{item.rows}:"
                f"{item.min_timestamp_ms}:{item.max_timestamp_ms}\n"
            ).encode()
        )
    return digest.hexdigest()


def discover_price_partitions(root: Path, legacy_path: Path | None = None) -> list[Path]:
    partitions = sorted(root.glob("????-??.parquet")) if root.exists() else []
    if partitions:
        return partitions
    if legacy_path is not None and legacy_path.exists():
        return [legacy_path]
    return []


def audit_price_store(
    root: Path,
    *,
    legacy_path: Path | None = None,
    minimum_coverage_ratio: float = DEFAULT_MINIMUM_COVERAGE,
) -> PriceIntegrityReport:
    """Build a reproducible integrity report for all available price files."""

    if not 0.0 < minimum_coverage_ratio <= 1.0:
        raise ValueError("minimum_coverage_ratio must be in (0, 1]")
    paths = discover_price_partitions(root, legacy_path)
    generated_at = datetime.now(UTC).isoformat()
    if not paths:
        return PriceIntegrityReport(
            status="WAIT",
            reason="no_price_partitions",
            generated_at=generated_at,
            root=str(root),
            source="observed_completed_1m_candles",
            partition_count=0,
            total_rows=0,
            unique_rows=0,
            expected_rows=0,
            missing_minutes=0,
            coverage_ratio=0.0,
            minimum_coverage_ratio=minimum_coverage_ratio,
            coverage_passed=False,
            structural_valid=False,
            min_timestamp_ms=None,
            max_timestamp_ms=None,
            dataset_fingerprint_sha256=None,
            partitions=(),
        )

    partitions = [audit_partition(path) for path in paths]
    partitions.sort(key=lambda item: item.min_timestamp_ms)
    total_rows = sum(item.rows for item in partitions)
    unique_rows = sum(item.unique_rows for item in partitions)
    minimum = min(item.min_timestamp_ms for item in partitions)
    maximum = max(item.max_timestamp_ms for item in partitions)
    expected_rows = int((maximum - minimum) // MINUTE_MS) + 1
    missing_minutes = max(0, expected_rows - unique_rows)
    coverage_ratio = unique_rows / expected_rows if expected_rows else 0.0
    structural_valid = all(item.structural_valid for item in partitions)
    coverage_passed = coverage_ratio >= minimum_coverage_ratio
    if not structural_valid:
        status = "FAIL"
        reason = "structural_price_integrity_failed"
    elif not coverage_passed:
        status = "WARN"
        reason = "minute_coverage_below_threshold"
    else:
        status = "PASS"
        reason = "price_integrity_and_coverage_passed"
    return PriceIntegrityReport(
        status=status,
        reason=reason,
        generated_at=generated_at,
        root=str(root),
        source="observed_completed_1m_candles",
        partition_count=len(partitions),
        total_rows=total_rows,
        unique_rows=unique_rows,
        expected_rows=expected_rows,
        missing_minutes=missing_minutes,
        coverage_ratio=coverage_ratio,
        minimum_coverage_ratio=minimum_coverage_ratio,
        coverage_passed=coverage_passed,
        structural_valid=structural_valid,
        min_timestamp_ms=minimum,
        max_timestamp_ms=maximum,
        dataset_fingerprint_sha256=_fingerprint(partitions),
        partitions=tuple(partitions),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit XASP observed price storage")
    parser.add_argument("--root", type=Path, default=Path("data/prices"))
    parser.add_argument("--legacy", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/data_integrity.json"),
    )
    parser.add_argument(
        "--minimum-coverage",
        type=float,
        default=DEFAULT_MINIMUM_COVERAGE,
    )
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = audit_price_store(
        args.root,
        legacy_path=args.legacy,
        minimum_coverage_ratio=args.minimum_coverage,
    )
    report.save(args.output)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if args.fail_on_error and report.status == "FAIL":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
