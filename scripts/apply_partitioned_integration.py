from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/xasp/anchor_dataset.py",
    "from .dataset_state import DatasetStateStore\nfrom .labeling import (\n",
    "from .dataset_state import DatasetStateStore\n"
    "from .horizons import RESEARCH_HORIZONS_MINUTES\n"
    "from .partitioned_horizon_store import (\n"
    "    HorizonPartitionKey,\n"
    "    HorizonStoreStats,\n"
    "    PartitionedHorizonStore,\n"
    ")\n"
    "from .labeling import (\n",
)
replace_once(
    "src/xasp/anchor_dataset.py",
    "    horizons_minutes: tuple[int, ...] = (15, 30, 45, 60)\n",
    "    horizons_minutes: tuple[int, ...] = RESEARCH_HORIZONS_MINUTES\n",
)
replace_once(
    "src/xasp/anchor_dataset.py",
    '''class AnchorDatasetStore:\n    """Append/replace a compact Parquet anchor table atomically."""\n\n    def __init__(self, path: Path) -> None:\n        self.path = path\n\n    def load(self) -> pd.DataFrame:\n        if not self.path.exists():\n            return pd.DataFrame(columns=ANCHOR_COLUMNS)\n        frame = pd.read_parquet(self.path)\n        missing = set(ANCHOR_COLUMNS) - set(frame.columns)\n        if missing:\n            raise ValueError(f"anchor dataset missing columns: {sorted(missing)}")\n        return frame[ANCHOR_COLUMNS].sort_values(\n            ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True\n        )\n\n    def save(self, frame: pd.DataFrame) -> None:\n        self.path.parent.mkdir(parents=True, exist_ok=True)\n        normalized = frame[ANCHOR_COLUMNS].sort_values(\n            ["anchor_timestamp_ms", "horizon_minutes"], ignore_index=True\n        )\n        temporary = self.path.with_suffix(self.path.suffix + ".tmp")\n        normalized.to_parquet(temporary, index=False)\n        temporary.replace(self.path)\n''',
    '''class AnchorDatasetStore:\n    """Monthly/horizon anchor partitions with legacy single-file migration."""\n\n    def __init__(self, path: Path) -> None:\n        self.path = path\n        self._store = PartitionedHorizonStore(\n            root=path.with_suffix(""),\n            legacy_path=path,\n            columns=tuple(ANCHOR_COLUMNS),\n            key_columns=("anchor_timestamp_ms", "horizon_minutes"),\n            timestamp_column="anchor_timestamp_ms",\n            horizon_column="horizon_minutes",\n            dataset_name="first_touch_anchors",\n            status_column="status",\n        )\n\n    @property\n    def root(self) -> Path:\n        return self._store.root\n\n    @property\n    def exists(self) -> bool:\n        return self._store.exists\n\n    @property\n    def needs_legacy_migration(self) -> bool:\n        return self._store.needs_legacy_migration\n\n    def ensure_ready(self) -> None:\n        self._store.ensure_ready()\n\n    def stats(self) -> HorizonStoreStats:\n        return self._store.stats()\n\n    def partition_keys(self) -> tuple[HorizonPartitionKey, ...]:\n        return self._store.partition_keys()\n\n    def has_partition(self, key: HorizonPartitionKey) -> bool:\n        return self._store.has_partition(key)\n\n    def partition_rows(self, key: HorizonPartitionKey) -> int:\n        return int(len(self._store.load_partition(key)))\n\n    def load_partition(self, key: HorizonPartitionKey) -> pd.DataFrame:\n        return self._store.load_partition(key)\n\n    def load(\n        self,\n        *,\n        horizons: tuple[int, ...] | None = None,\n        start_ms: int | None = None,\n        end_ms: int | None = None,\n        statuses: tuple[str, ...] | None = None,\n    ) -> pd.DataFrame:\n        return self._store.load(\n            horizons=horizons,\n            start_ms=start_ms,\n            end_ms=end_ms,\n            statuses=statuses,\n        )\n\n    def upsert(self, frame: pd.DataFrame) -> HorizonStoreStats:\n        return self._store.upsert(frame)\n\n    def save(self, frame: pd.DataFrame) -> None:\n        self._store.replace(frame)\n''',
)

replace_once(
    "src/xasp/pipeline.py",
    "from .fast_anchor_dataset import update_anchor_dataset_from_candles_fast\n",
    "from .partitioned_anchor_builder import (\n"
    "    AnchorBuildResult,\n"
    "    build_partitioned_anchor_dataset,\n"
    ")\n",
)
replace_once(
    "src/xasp/pipeline.py",
    "    price_partition_count: int = 0\n",
    "    price_partition_count: int = 0\n    anchor_partition_count: int = 0\n",
)
replace_once(
    "src/xasp/pipeline.py",
    '''        self.price_store = PartitionedPriceStore(\n            paths.price_partitions,\n            legacy_path=paths.prices,\n        )\n''',
    '''        self.price_store = PartitionedPriceStore(\n            paths.price_partitions,\n            legacy_path=paths.prices,\n        )\n        self.last_anchor_build_result: AnchorBuildResult | None = None\n''',
)
replace_once(
    "src/xasp/pipeline.py",
    '''        prices = self.price_store.load()\n        anchors = update_anchor_dataset_from_candles_fast(\n            _to_candles(prices),\n            AnchorDatasetStore(self.paths.anchors),\n            state_store,\n            self.config.anchor_config,\n        )\n        state = state_store.load()\n''',
    '''        prices = self.price_store.load()\n        anchor_build = build_partitioned_anchor_dataset(\n            prices,\n            AnchorDatasetStore(self.paths.anchors),\n            state_store,\n            self.config.anchor_config,\n        )\n        self.last_anchor_build_result = anchor_build\n        state = state_store.load()\n''',
)
replace_once(
    "src/xasp/pipeline.py",
    '''            total_price_rows=stats.total_rows,\n            anchor_rows=len(anchors),\n            pending_labels=state.pending_label_count,\n            finalized_labels=state.finalized_label_count,\n            checkpoint_writes=checkpoint_writes,\n            price_partition_count=stats.partition_count,\n''',
    '''            total_price_rows=stats.total_rows,\n            anchor_rows=anchor_build.stats.total_rows,\n            pending_labels=state.pending_label_count,\n            finalized_labels=state.finalized_label_count,\n            checkpoint_writes=checkpoint_writes,\n            price_partition_count=stats.partition_count,\n            anchor_partition_count=anchor_build.stats.partition_count,\n''',
)

replace_once(
    "src/xasp/platform_runtime.py",
    '''        anchors = AnchorDatasetStore(self.paths.anchors).load()\n        final_rows = int((anchors["status"] == "FINAL").sum()) if not anchors.empty else 0\n        pending_rows = int((anchors["status"] == "PENDING").sum()) if not anchors.empty else 0\n        self._status.price_rows = len(prices)\n        self._status.anchor_rows = len(anchors)\n        self._status.final_rows = final_rows\n        self._status.pending_rows = pending_rows\n''',
    '''        anchor_stats = AnchorDatasetStore(self.paths.anchors).stats()\n        self._status.price_rows = len(prices)\n        self._status.anchor_rows = anchor_stats.total_rows\n        self._status.final_rows = anchor_stats.final_rows\n        self._status.pending_rows = anchor_stats.pending_rows\n''',
)

replace_once(
    "src/xasp/extended_runtime.py",
    "from .anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetConfig, AnchorDatasetStore\n",
    "from .anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore\n",
)
replace_once(
    "src/xasp/extended_runtime.py",
    "from .dataset_state import DatasetStateStore\n"
    "from .fast_anchor_dataset import _initial_dataset, _normalize_candles, _save_and_advance_state\n",
    "",
)
replace_once(
    "src/xasp/extended_runtime.py",
    "from .labeling import CandlePoint\n",
    "from .partitioned_horizon_store import HorizonStoreStats\n",
)
start_marker = "    def _ensure_extended_anchor_horizons(self) -> pd.DataFrame:\n"
end_marker = "    def sync_real_data(self, end_ms: int | None = None) -> None:\n"
path = Path("src/xasp/extended_runtime.py")
content = path.read_text(encoding="utf-8")
start = content.index(start_marker)
end = content.index(end_marker, start)
replacement = '''    def _ensure_extended_anchor_horizons(self) -> HorizonStoreStats:\n        store = AnchorDatasetStore(self.paths.anchors)\n        stats = store.stats()\n        available = {horizon for horizon, rows in stats.horizon_rows.items() if rows > 0}\n        if stats.total_rows and available != set(HORIZONS):\n            missing = sorted(set(HORIZONS) - available)\n            raise RuntimeError(f"extended anchor partitions missing horizons: {missing}")\n        return stats\n\n'''
path.write_text(content[:start] + replacement + content[end:], encoding="utf-8")
replace_once(
    "src/xasp/extended_runtime.py",
    '''        anchors = self._ensure_extended_anchor_horizons()\n        self.status.anchor_rows = int(len(anchors))\n        self.status.final_rows = int((anchors["status"] == "FINAL").sum())\n        self.status.pending_rows = int((anchors["status"] == "PENDING").sum())\n''',
    '''        anchors = self._ensure_extended_anchor_horizons()\n        self.status.anchor_rows = anchors.total_rows\n        self.status.final_rows = anchors.final_rows\n        self.status.pending_rows = anchors.pending_rows\n''',
)

replace_once(
    "src/xasp/memory_safe_runtime.py",
    '''        anchors = AnchorDatasetStore(self.paths.anchors).load()\n        features = pd.read_parquet(self.paths.features)\n        final_count = int((anchors["status"] == "FINAL").sum())\n''',
    '''        anchor_store = AnchorDatasetStore(self.paths.anchors)\n        anchor_stats = anchor_store.stats()\n        features = pd.read_parquet(self.paths.features)\n        final_count = anchor_stats.final_rows\n''',
)
replace_once(
    "src/xasp/memory_safe_runtime.py",
    '''            anchor_subset = anchors[anchors["horizon_minutes"] == horizon].copy()\n''',
    '''            anchor_subset = anchor_store.load(\n                horizons=(horizon,),\n                statuses=("FINAL",),\n            )\n''',
)

replace_once(
    "src/xasp/envelope_engine_v2.py",
    "import pandas as pd\n\nfrom .fast_future_envelope import build_future_envelope_targets_fast\n",
    "import pandas as pd\n\n"
    "from .anchor_dataset import AnchorDatasetStore\n"
    "from .envelope_target_store import (\n"
    "    EnvelopeTargetStore,\n"
    "    sync_envelope_targets_from_anchors,\n"
    ")\n"
    "from .fast_future_envelope import build_future_envelope_targets_fast\n",
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    '''        self.paths = paths\n        self.bundle: dict[str, Any] | None = None\n''',
    '''        self.paths = paths\n        self.target_store = EnvelopeTargetStore(paths.targets)\n        self.bundle: dict[str, Any] | None = None\n''',
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    '''        self.paths.targets.parent.mkdir(parents=True, exist_ok=True)\n        temporary = self.paths.targets.with_suffix(".parquet.tmp")\n        targets.to_parquet(temporary, index=False)\n        temporary.replace(self.paths.targets)\n        return targets\n''',
    '''        self.target_store.replace(targets)\n        return targets\n\n    def sync_targets_from_anchors(\n        self,\n        anchor_store: AnchorDatasetStore,\n        *,\n        changed_partitions: tuple[Any, ...] | None = None,\n    ) -> None:\n        sync_envelope_targets_from_anchors(\n            anchor_store,\n            self.target_store,\n            changed_anchor_partitions=changed_partitions,\n        )\n''',
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    "        targets: pd.DataFrame,\n",
    "        targets: pd.DataFrame | None,\n",
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    "        if targets.empty:\n",
    "        if targets is not None and targets.empty:\n",
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    '''            target_subset = targets[targets["horizon_minutes"] == horizon].copy()\n''',
    '''            target_subset = (\n                self.target_store.load(\n                    horizons=(horizon,),\n                    statuses=("FINAL",),\n                )\n                if targets is None\n                else targets[targets["horizon_minutes"] == horizon].copy()\n            )\n''',
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    '''        bundle: dict[str, Any] = {\n''',
    '''        target_stats = self.target_store.stats()\n        bundle: dict[str, Any] = {\n''',
)
replace_once(
    "src/xasp/envelope_engine_v2.py",
    '''            "training_final_rows": int(training_final_rows),\n            "promoted_for_trading": False,\n''',
    '''            "training_final_rows": int(training_final_rows),\n            "target_rows": target_stats.total_rows,\n            "target_partition_count": target_stats.partition_count,\n            "promoted_for_trading": False,\n''',
)

replace_once(
    "src/xasp/platform_runtime_v2.py",
    '''        """Sync candles and anchors; defer the large Model A target rebuild until training."""\n\n        super().sync_real_data(end_ms)\n        self._refresh_research_state()\n''',
    '''        """Sync candles, anchors, and only changed Model A target partitions."""\n\n        super().sync_real_data(end_ms)\n        changed = (\n            ()\n            if self.pipeline.last_anchor_build_result is None\n            else self.pipeline.last_anchor_build_result.changed_partitions\n        )\n        self.envelope.sync_targets_from_anchors(\n            AnchorDatasetStore(self.paths.anchors),\n            changed_partitions=changed,\n        )\n        self._refresh_research_state()\n''',
)
replace_once(
    "src/xasp/platform_runtime_v2.py",
    '''        anchors = AnchorDatasetStore(self.paths.anchors).load()\n        final_count = int((anchors["status"] == "FINAL").sum()) if not anchors.empty else 0\n        per_horizon_counts = (\n            anchors[anchors["status"] == "FINAL"]\n            .groupby("horizon_minutes")\n            .size()\n            .to_dict()\n            if not anchors.empty\n            else {}\n        )\n        enough_rows = any(\n            int(per_horizon_counts.get(horizon, 0))\n''',
    '''        anchor_stats = AnchorDatasetStore(self.paths.anchors).stats()\n        target_stats = self.envelope.target_store.stats()\n        final_count = anchor_stats.final_rows\n        enough_rows = any(\n            int(target_stats.horizon_rows.get(horizon, 0))\n''',
)
start_marker = '''        had_envelope_champion = self.envelope.bundle is not None\n        self._set_lifecycle(\n            "BUILD_TARGETS_A",\n'''
end_marker = '''        self._set_lifecycle(\n            "TRAIN_MODEL_A",\n'''
path = Path("src/xasp/platform_runtime_v2.py")
content = path.read_text(encoding="utf-8")
start = content.index(start_marker)
end = content.index(end_marker, start)
path.write_text(
    content[:start]
    + '''        had_envelope_champion = self.envelope.bundle is not None\n        features = pd.read_parquet(self.paths.features)\n'''
    + content[end:],
    encoding="utf-8",
)
replace_once(
    "src/xasp/platform_runtime_v2.py",
    '''        envelope_trained = self.envelope.train(\n            targets,\n''',
    '''        envelope_trained = self.envelope.train(\n            None,\n''',
)

replace_once(
    "src/xasp/platform_api.py",
    '''            "shock_targets": platform.envelope.paths.targets.exists(),\n''',
    '''            "shock_targets": platform.envelope.target_store.exists,\n            "shock_target_partitions": platform.envelope.target_store.stats().partition_count,\n''',
)
replace_once(
    "pyproject.toml",
    'version = "1.4.2"\n',
    'version = "1.5.0"\n',
)
