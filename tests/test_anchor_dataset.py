from pathlib import Path

from xasp.anchor_dataset import (
    AnchorDatasetConfig,
    AnchorDatasetStore,
    update_anchor_dataset,
)
from xasp.dataset_state import DatasetStateStore
from xasp.labeling import PricePoint


def test_incremental_anchor_dataset_resumes_and_finalizes(tmp_path: Path) -> None:
    dataset_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(15,), cadence_ms=60_000)

    initial = [
        PricePoint(timestamp_ms=0, price=1.0),
        PricePoint(timestamp_ms=5 * 60_000, price=1.02),
        PricePoint(timestamp_ms=10 * 60_000, price=1.03),
    ]
    first = update_anchor_dataset(initial, dataset_store, state_store, config)
    assert not first.empty
    assert set(first["status"]) == {"PENDING"}

    resumed = initial + [
        PricePoint(timestamp_ms=15 * 60_000, price=1.11),
        PricePoint(timestamp_ms=20 * 60_000, price=1.12),
        PricePoint(timestamp_ms=25 * 60_000, price=1.13),
    ]
    second = update_anchor_dataset(resumed, dataset_store, state_store, config)

    assert second.duplicated(["anchor_timestamp_ms", "horizon_minutes"]).sum() == 0
    matured = second[second["anchor_timestamp_ms"] == 60_000].iloc[0]
    assert matured["status"] == "FINAL"
    assert matured["label"] == "UP_10"
    assert matured["max_price"] == 1.11
    assert matured["min_price"] == 1.02


def test_restart_does_not_recreate_existing_anchors(tmp_path: Path) -> None:
    dataset_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(15, 30), cadence_ms=60_000)
    prices = [PricePoint(timestamp_ms=i * 60_000, price=1 + i / 1000) for i in range(40)]

    first = update_anchor_dataset(prices, dataset_store, state_store, config)
    second = update_anchor_dataset(prices, dataset_store, state_store, config)

    assert len(second) == len(first)
    assert second.duplicated(["anchor_timestamp_ms", "horizon_minutes"]).sum() == 0


def test_state_counters_match_dataset(tmp_path: Path) -> None:
    dataset_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(15,), cadence_ms=60_000)
    prices = [PricePoint(timestamp_ms=i * 60_000, price=1.0) for i in range(20)]

    frame = update_anchor_dataset(prices, dataset_store, state_store, config)
    state = state_store.load()

    assert state.pending_label_count == int((frame["status"] == "PENDING").sum())
    assert state.finalized_label_count == int((frame["status"] == "FINAL").sum())
