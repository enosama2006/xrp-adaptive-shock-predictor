from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:100]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/xasp/partitioned_horizon_store.py",
    "import json\nimport shutil\n",
    "import json\nimport shutil\nfrom collections.abc import Iterable\n",
)
replace_once(
    "src/xasp/partitioned_horizon_store.py",
    '''    def upsert(\n        self,\n        incoming: pd.DataFrame,\n        *,\n        migrated_from_legacy: bool | None = None,\n    ) -> HorizonStoreStats:\n        normalized = self.normalize(incoming)\n        if normalized.empty:\n            self.ensure_ready()\n            return self.stats()\n        self.root.mkdir(parents=True, exist_ok=True)\n        identifiers = self._keys_for_frame(normalized)\n        for identifier, group in normalized.groupby(identifiers, sort=True):\n            key = self._parse_identifier(str(identifier))\n            path = self._partition_path(key)\n            if path.exists():\n                existing = self.normalize(pd.read_parquet(path))\n                merged = self.normalize(pd.concat([existing, group], ignore_index=True))\n            else:\n                merged = self.normalize(group)\n            if any(int(value) != key.horizon_minutes for value in merged[self.horizon_column]):\n                raise ValueError(f"mixed horizons in upsert partition: {key.identifier}")\n            if any(\n                self._month_key(int(value)) != key.month for value in merged[self.timestamp_column]\n            ):\n                raise ValueError(f"mixed months in upsert partition: {key.identifier}")\n            self._atomic_write_parquet(merged, path)\n        self.rebuild_manifest(migrated_from_legacy=migrated_from_legacy)\n        return self.stats()\n''',
    '''    def _write_normalized(self, normalized: pd.DataFrame) -> None:\n        identifiers = self._keys_for_frame(normalized)\n        for identifier, group in normalized.groupby(identifiers, sort=True):\n            key = self._parse_identifier(str(identifier))\n            path = self._partition_path(key)\n            if path.exists():\n                existing = self.normalize(pd.read_parquet(path))\n                merged = self.normalize(pd.concat([existing, group], ignore_index=True))\n            else:\n                merged = self.normalize(group)\n            if any(int(value) != key.horizon_minutes for value in merged[self.horizon_column]):\n                raise ValueError(f"mixed horizons in upsert partition: {key.identifier}")\n            if any(\n                self._month_key(int(value)) != key.month for value in merged[self.timestamp_column]\n            ):\n                raise ValueError(f"mixed months in upsert partition: {key.identifier}")\n            self._atomic_write_parquet(merged, path)\n\n    def upsert_frames(\n        self,\n        frames: Iterable[pd.DataFrame],\n        *,\n        migrated_from_legacy: bool | None = None,\n    ) -> HorizonStoreStats:\n        """Stream multiple frames and rebuild the manifest exactly once."""\n\n        self.root.mkdir(parents=True, exist_ok=True)\n        wrote_any = False\n        for frame in frames:\n            normalized = self.normalize(frame)\n            if normalized.empty:\n                continue\n            self._write_normalized(normalized)\n            wrote_any = True\n        if not wrote_any:\n            self.ensure_ready()\n            return self.stats()\n        self.rebuild_manifest(migrated_from_legacy=migrated_from_legacy)\n        return self.stats()\n\n    def upsert(\n        self,\n        incoming: pd.DataFrame,\n        *,\n        migrated_from_legacy: bool | None = None,\n    ) -> HorizonStoreStats:\n        return self.upsert_frames(\n            (incoming,),\n            migrated_from_legacy=migrated_from_legacy,\n        )\n''',
)
replace_once(
    "src/xasp/partitioned_horizon_store.py",
    '''    def has_partition(self, key: HorizonPartitionKey) -> bool:\n        self.ensure_ready()\n        return self._partition_path(key).exists()\n\n    def load_partition(self, key: HorizonPartitionKey) -> pd.DataFrame:\n''',
    '''    def has_partition(self, key: HorizonPartitionKey) -> bool:\n        self.ensure_ready()\n        return self._partition_path(key).exists()\n\n    def partition_rows(self, key: HorizonPartitionKey) -> int:\n        """Return row count from manifest metadata without reading Parquet data."""\n\n        self.ensure_ready()\n        manifest = self._read_manifest()\n        if manifest is None:\n            return 0\n        raw = manifest.get("partitions", [])\n        if not isinstance(raw, list):\n            raise ValueError(f"{self.dataset_name} manifest partitions must be a list")\n        for item in raw:\n            if not isinstance(item, dict):\n                raise ValueError("partition manifest entry must be an object")\n            if (\n                int(item["horizon_minutes"]) == key.horizon_minutes\n                and str(item["month"]) == key.month\n            ):\n                return int(item["rows"])\n        return 0\n\n    def load_partition(self, key: HorizonPartitionKey) -> pd.DataFrame:\n''',
)

replace_once(
    "src/xasp/anchor_dataset.py",
    '''    def partition_rows(self, key: HorizonPartitionKey) -> int:\n        return int(len(self._store.load_partition(key)))\n''',
    '''    def partition_rows(self, key: HorizonPartitionKey) -> int:\n        return self._store.partition_rows(key)\n''',
)
replace_once(
    "src/xasp/anchor_dataset.py",
    '''    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:\n        return self._store.upsert(frame)\n\n    def save(self, frame: pd.DataFrame) -> None:\n''',
    '''    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:\n        return self._store.upsert(frame)\n\n    def upsert_frames(self, frames: Iterable[pd.DataFrame]) -> HorizonStoreStats:\n        return self._store.upsert_frames(frames)\n\n    def save(self, frame: pd.DataFrame) -> None:\n''',
)

builder_path = Path("src/xasp/partitioned_anchor_builder.py")
builder = builder_path.read_text(encoding="utf-8")
start = builder.index('    changed: list[HorizonPartitionKey] = []\n')
end = builder.index('    stats = store.stats()\n', start)
new_builder = '''    changed: list[HorizonPartitionKey] = []\n    rebuilt_months: list[str] = []\n\n    def partition_frames() -> object:\n        for month in sorted(months.unique().tolist()):\n            month_mask = months == month\n            month_prices = frame.loc[month_mask]\n            if month_prices.empty:\n                continue\n            month_start_ms = int(month_prices["timestamp_ms"].min())\n            month_last_ms = int(month_prices["timestamp_ms"].max())\n            expected_anchor_rows = int(len(month_prices))\n            if not _partition_needs_rebuild(\n                store=store,\n                month=str(month),\n                horizons=horizons,\n                expected_anchor_rows=expected_anchor_rows,\n                month_last_timestamp_ms=month_last_ms,\n                latest_timestamp_ms=latest_timestamp_ms,\n                maximum_horizon_ms=maximum_horizon_ms,\n            ):\n                continue\n\n            source_end_ms = month_last_ms + maximum_horizon_ms\n            source = frame[\n                (frame["timestamp_ms"] >= month_start_ms)\n                & (frame["timestamp_ms"] <= source_end_ms)\n            ]\n            built = _initial_dataset(\n                _to_candles(source),\n                config,\n                chunk_rows=chunk_rows,\n            )\n            partition_rows = built[\n                (built["anchor_timestamp_ms"] >= month_start_ms)\n                & (built["anchor_timestamp_ms"] <= month_last_ms)\n                & (built["horizon_minutes"].isin(horizons))\n            ].copy()\n            if len(partition_rows) != expected_anchor_rows * len(horizons):\n                raise RuntimeError(\n                    "anchor partition build did not produce one row per minute/horizon: "\n                    f"month={month}, expected={expected_anchor_rows * len(horizons)}, "\n                    f"actual={len(partition_rows)}"\n                )\n            rebuilt_months.append(str(month))\n            changed.extend(\n                HorizonPartitionKey(horizon, str(month)) for horizon in horizons\n            )\n            yield partition_rows.reindex(columns=ANCHOR_COLUMNS)\n\n    stats = store.upsert_frames(partition_frames())\n'''
builder_path.write_text(builder[:start] + new_builder + builder[end + len('    stats = store.stats()\n'):], encoding="utf-8")
replace_once(
    "src/xasp/partitioned_anchor_builder.py",
    "from dataclasses import dataclass\n",
    "from collections.abc import Iterator\nfrom dataclasses import dataclass\n",
)
replace_once(
    "src/xasp/partitioned_anchor_builder.py",
    "    def partition_frames() -> object:\n",
    "    def partition_frames() -> Iterator[pd.DataFrame]:\n",
)

replace_once(
    "src/xasp/envelope_target_store.py",
    '''    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:\n        return self._store.upsert(frame)\n\n    def replace(self, frame: pd.DataFrame) -> HorizonStoreStats:\n''',
    '''    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:\n        return self._store.upsert(frame)\n\n    def upsert_frames(self, frames: object) -> HorizonStoreStats:\n        return self._store.upsert_frames(frames)\n\n    def replace(self, frame: pd.DataFrame) -> HorizonStoreStats:\n''',
)
replace_once(
    "src/xasp/envelope_target_store.py",
    "from dataclasses import dataclass\n",
    "from collections.abc import Iterable, Iterator\nfrom dataclasses import dataclass\n",
)
replace_once(
    "src/xasp/envelope_target_store.py",
    "    def upsert_frames(self, frames: object) -> HorizonStoreStats:\n",
    "    def upsert_frames(self, frames: Iterable[pd.DataFrame]) -> HorizonStoreStats:\n",
)
replace_once(
    "src/xasp/envelope_target_store.py",
    '''    changed: list[HorizonPartitionKey] = []\n    for key in sorted(selected, key=lambda value: (value.month, value.horizon_minutes)):\n        anchors = anchor_store.load_partition(key)\n        targets = _targets_from_anchor_partition(anchors)\n        if targets.empty:\n            continue\n        target_store.upsert(targets)\n        changed.append(key)\n    return EnvelopeTargetSyncResult(target_store.stats(), tuple(changed))\n''',
    '''    changed: list[HorizonPartitionKey] = []\n\n    def target_frames() -> Iterator[pd.DataFrame]:\n        for key in sorted(\n            selected,\n            key=lambda value: (value.month, value.horizon_minutes),\n        ):\n            anchors = anchor_store.load_partition(key)\n            targets = _targets_from_anchor_partition(anchors)\n            if targets.empty:\n                continue\n            changed.append(key)\n            yield targets\n\n    stats = target_store.upsert_frames(target_frames())\n    return EnvelopeTargetSyncResult(stats, tuple(changed))\n''',
)

replace_once(
    "tests/test_partitioned_horizon_store.py",
    '''    assert store.has_partition(HorizonPartitionKey(15, "2025-01"))\n    assert store.has_partition(HorizonPartitionKey(60, "2025-02"))\n''',
    '''    january_key = HorizonPartitionKey(15, "2025-01")\n    assert store.has_partition(january_key)\n    assert store.partition_rows(january_key) == 1\n    assert store.has_partition(HorizonPartitionKey(60, "2025-02"))\n''',
)
