from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from xasp.anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore
from xasp.dataset_state import DatasetStateStore
from xasp.partitioned_anchor_builder import build_partitioned_anchor_dataset
from xasp.partitioned_horizon_store import HorizonPartitionKey

MINUTE = 60_000


def _prices(start_ms: int, rows: int) -> pd.DataFrame:
    values = [1.0 + index / 10_000 for index in range(rows)]
    return pd.DataFrame(
        {
            "timestamp_ms": [start_ms + index * MINUTE for index in range(rows)],
            "price": values,
            "open": values,
            "high": [value * 1.001 for value in values],
            "low": [value * 0.999 for value in values],
        }
    )


def test_builder_writes_one_partition_per_month_and_horizon(tmp_path: Path) -> None:
    start = int(datetime(2025, 1, 31, 23, 50, tzinfo=UTC).timestamp() * 1000)
    prices = _prices(start, 30)
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(2, 4))

    result = build_partitioned_anchor_dataset(prices, store, state_store, config)

    assert result.stats.total_rows == len(prices) * 2
    assert result.stats.partition_count == 4
    assert set(result.rebuilt_months) == {"2025-01", "2025-02"}
    assert store.has_partition(HorizonPartitionKey(2, "2025-01"))
    assert store.has_partition(HorizonPartitionKey(4, "2025-02"))
    state = state_store.load()
    assert state.pending_label_count == result.stats.pending_rows
    assert state.finalized_label_count == result.stats.final_rows


def test_builder_skips_completed_history_and_rebuilds_live_tail(tmp_path: Path) -> None:
    start = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    prices = _prices(start, 70)
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(2, 4))

    first = build_partitioned_anchor_dataset(prices, store, state_store, config)
    second = build_partitioned_anchor_dataset(prices, store, state_store, config)

    assert first.stats.total_rows == second.stats.total_rows
    assert second.rebuilt_months == ("2025-01",)
    assert len(second.changed_partitions) == 2


def test_builder_repairs_partition_when_older_rows_extend_same_month(tmp_path: Path) -> None:
    month_start = int(datetime(2025, 3, 1, tzinfo=UTC).timestamp() * 1000)
    later = _prices(month_start + 10 * MINUTE, 20)
    full = _prices(month_start, 30)
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(2,))

    build_partitioned_anchor_dataset(later, store, state_store, config)
    result = build_partitioned_anchor_dataset(full, store, state_store, config)

    assert result.stats.total_rows == len(full)
    loaded = store.load_partition(HorizonPartitionKey(2, "2025-03"))
    assert int(loaded["anchor_timestamp_ms"].min()) == month_start
