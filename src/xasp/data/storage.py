from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd

from .contracts import DatasetManifest, MarketRecord, file_digest, utc_now
from .quality import validate_records


def _rows(records: list[MarketRecord]) -> list[dict[str, object]]:
    return [
        {
            "venue": record.venue,
            "symbol": record.symbol,
            "record_type": record.record_type,
            "event_time_ms": record.event_time_ms,
            "received_time_ms": record.received_time_ms,
            "source_sequence": record.source_sequence,
            "payload_json": json.dumps(
                record.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        }
        for record in records
    ]


def write_dataset(
    records: list[MarketRecord],
    *,
    output_dir: Path,
    source: str,
    notes: list[str] | None = None,
) -> DatasetManifest:
    if not records:
        raise ValueError("cannot write an empty dataset")

    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        records,
        key=lambda row: (
            row.event_time_ms,
            row.venue,
            row.symbol,
            row.record_type,
            row.source_sequence or -1,
        ),
    )
    quality = validate_records(ordered)
    dataset_id = f"xasp-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    parquet_path = output_dir / f"{dataset_id}.parquet"
    pd.DataFrame(_rows(ordered)).to_parquet(parquet_path, index=False)

    manifest = DatasetManifest(
        dataset_id=dataset_id,
        created_at_utc=utc_now(),
        source=source,
        symbols=sorted({row.symbol for row in ordered}),
        start_event_time_ms=ordered[0].event_time_ms,
        end_event_time_ms=ordered[-1].event_time_ms,
        files=[file_digest(parquet_path, rows=len(ordered))],
        row_count=len(ordered),
        duplicate_count=quality.duplicates,
        invalid_count=quality.invalid,
        notes=list(notes or []),
    )
    manifest_path = output_dir / f"{dataset_id}.manifest.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest
