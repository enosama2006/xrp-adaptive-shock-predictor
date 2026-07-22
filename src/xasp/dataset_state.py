"""Durable state for resumable ingestion and incremental dataset construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(slots=True)
class DatasetState:
    """Versioned watermarks and counters for restart-safe incremental processing."""

    schema_version: int = 1
    dataset_id: str = "xasp-default"
    raw_watermarks_ms: dict[str, int] = field(default_factory=dict)
    feature_watermark_ms: int | None = None
    finalized_label_watermark_ms: int | None = None
    last_training_cutoff_ms: int | None = None
    last_model_version: str | None = None
    pending_label_count: int = 0
    finalized_label_count: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"unsupported dataset state schema: {self.schema_version}")
        for name, value in self.raw_watermarks_ms.items():
            if not name or value < 0:
                raise ValueError("raw watermarks require non-empty names and non-negative values")
        for value in (
            self.feature_watermark_ms,
            self.finalized_label_watermark_ms,
            self.last_training_cutoff_ms,
        ):
            if value is not None and value < 0:
                raise ValueError("watermarks must be non-negative")
        if self.pending_label_count < 0 or self.finalized_label_count < 0:
            raise ValueError("label counters must be non-negative")

    def advance_raw_watermark(self, source_key: str, verified_timestamp_ms: int) -> None:
        """Advance one raw watermark monotonically after a verified durable write."""
        if not source_key:
            raise ValueError("source_key is required")
        if verified_timestamp_ms < 0:
            raise ValueError("verified_timestamp_ms must be non-negative")
        previous = self.raw_watermarks_ms.get(source_key)
        if previous is not None and verified_timestamp_ms < previous:
            raise ValueError("watermarks cannot move backwards")
        self.raw_watermarks_ms[source_key] = verified_timestamp_ms
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DatasetState:
        state = cls(**payload)
        state.validate()
        return state


class DatasetStateStore:
    """JSON state store using atomic replace to avoid partial-file corruption."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> DatasetState:
        if not self.path.exists():
            return DatasetState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("dataset state must be a JSON object")
        return DatasetState.from_dict(payload)

    def save(self, state: DatasetState) -> None:
        state.touch()
        payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            temporary_path = Path(handle.name)
        temporary_path.replace(self.path)
