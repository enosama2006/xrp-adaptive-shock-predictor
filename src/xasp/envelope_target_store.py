"""Partitioned storage and anchor-derived target materialization for Model A."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .partitioned_horizon_store import (
    HorizonPartitionKey,
    HorizonStoreStats,
    PartitionedHorizonStore,
)

ENVELOPE_TARGET_COLUMNS: tuple[str, ...] = (
    "anchor_timestamp_ms",
    "anchor_price",
    "horizon_minutes",
    "horizon_end_ms",
    "future_max_price",
    "future_min_price",
    "future_max_return",
    "future_min_return",
    "minutes_to_max",
    "minutes_to_min",
    "hit_up_02",
    "hit_up_05",
    "hit_up_10",
    "hit_down_02",
    "hit_down_05",
    "hit_down_10",
    "status",
)


@dataclass(frozen=True, slots=True)
class EnvelopeTargetSyncResult:
    stats: HorizonStoreStats
    changed_partitions: tuple[HorizonPartitionKey, ...]


class EnvelopeTargetStore:
    """Monthly/horizon target partitions with legacy single-file migration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = PartitionedHorizonStore(
            root=path.with_suffix(""),
            legacy_path=path,
            columns=ENVELOPE_TARGET_COLUMNS,
            key_columns=("anchor_timestamp_ms", "horizon_minutes"),
            timestamp_column="anchor_timestamp_ms",
            horizon_column="horizon_minutes",
            dataset_name="future_envelope_targets",
            status_column="status",
        )

    @property
    def root(self) -> Path:
        return self._store.root

    @property
    def exists(self) -> bool:
        return self._store.exists

    @property
    def needs_legacy_migration(self) -> bool:
        return self._store.needs_legacy_migration

    def ensure_ready(self) -> None:
        self._store.ensure_ready()

    def stats(self) -> HorizonStoreStats:
        return self._store.stats()

    def partition_keys(self) -> tuple[HorizonPartitionKey, ...]:
        return self._store.partition_keys()

    def has_partition(self, key: HorizonPartitionKey) -> bool:
        return self._store.has_partition(key)

    def load_partition(self, key: HorizonPartitionKey) -> pd.DataFrame:
        return self._store.load_partition(key)

    def load(
        self,
        *,
        horizons: tuple[int, ...] | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        return self._store.load(
            horizons=horizons,
            start_ms=start_ms,
            end_ms=end_ms,
            statuses=statuses,
        )

    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:
        return self._store.upsert(frame)

    def replace(self, frame: pd.DataFrame) -> HorizonStoreStats:
        return self._store.replace(frame)


def _targets_from_anchor_partition(anchors: pd.DataFrame) -> pd.DataFrame:
    if anchors.empty:
        return pd.DataFrame(columns=list(ENVELOPE_TARGET_COLUMNS))
    required = {
        "anchor_timestamp_ms",
        "anchor_price",
        "horizon_minutes",
        "horizon_end_ms",
        "max_price",
        "min_price",
        "max_return",
        "min_return",
        "status",
        "reason",
    }
    missing = required - set(anchors.columns)
    if missing:
        raise ValueError(f"anchor target source missing columns: {sorted(missing)}")

    valid = anchors[
        anchors["max_price"].notna()
        & anchors["min_price"].notna()
        & (
            (anchors["status"] == "FINAL")
            | (anchors["reason"] == "both_barriers_touched_in_same_candle")
        )
    ].copy()
    if valid.empty:
        return pd.DataFrame(columns=list(ENVELOPE_TARGET_COLUMNS))
    anchor_price = valid["anchor_price"].astype(float)
    max_price = valid["max_price"].astype(float)
    min_price = valid["min_price"].astype(float)
    output = pd.DataFrame(
        {
            "anchor_timestamp_ms": valid["anchor_timestamp_ms"].astype("int64"),
            "anchor_price": anchor_price,
            "horizon_minutes": valid["horizon_minutes"].astype("int64"),
            "horizon_end_ms": valid["horizon_end_ms"].astype("int64"),
            "future_max_price": max_price,
            "future_min_price": min_price,
            "future_max_return": valid["max_return"].astype(float),
            "future_min_return": valid["min_return"].astype(float),
            "minutes_to_max": pd.Series(pd.NA, index=valid.index, dtype="Int64"),
            "minutes_to_min": pd.Series(pd.NA, index=valid.index, dtype="Int64"),
            "hit_up_02": max_price >= anchor_price * 1.02,
            "hit_up_05": max_price >= anchor_price * 1.05,
            "hit_up_10": max_price >= anchor_price * 1.10,
            "hit_down_02": min_price <= anchor_price * 0.98,
            "hit_down_05": min_price <= anchor_price * 0.95,
            "hit_down_10": min_price <= anchor_price * 0.90,
            "status": "FINAL",
        }
    )
    return output.reindex(columns=list(ENVELOPE_TARGET_COLUMNS)).reset_index(drop=True)


def sync_envelope_targets_from_anchors(
    anchor_store: AnchorDatasetStore,
    target_store: EnvelopeTargetStore,
    *,
    changed_anchor_partitions: tuple[HorizonPartitionKey, ...] | None = None,
) -> EnvelopeTargetSyncResult:
    """Materialize only missing or changed target partitions from anchor evidence."""

    target_store.ensure_ready()
    anchor_keys = set(anchor_store.partition_keys())
    missing = {key for key in anchor_keys if not target_store.has_partition(key)}
    selected = missing
    if changed_anchor_partitions is not None:
        selected |= set(changed_anchor_partitions)
    elif not target_store.exists:
        selected = anchor_keys

    changed: list[HorizonPartitionKey] = []
    for key in sorted(selected, key=lambda value: (value.month, value.horizon_minutes)):
        anchors = anchor_store.load_partition(key)
        targets = _targets_from_anchor_partition(anchors)
        if targets.empty:
            continue
        target_store.upsert(targets)
        changed.append(key)
    return EnvelopeTargetSyncResult(target_store.stats(), tuple(changed))


__all__ = [
    "ENVELOPE_TARGET_COLUMNS",
    "EnvelopeTargetStore",
    "EnvelopeTargetSyncResult",
    "sync_envelope_targets_from_anchors",
]
