from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from xasp.anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetStore
from xasp.envelope_target_store import (
    EnvelopeTargetStore,
    sync_envelope_targets_from_anchors,
)
from xasp.partitioned_horizon_store import HorizonPartitionKey


def _timestamp() -> int:
    return int(datetime(2025, 5, 1, tzinfo=UTC).timestamp() * 1000)


def _anchor_frame() -> pd.DataFrame:
    timestamp = _timestamp()
    return pd.DataFrame(
        [
            {
                "anchor_timestamp_ms": timestamp,
                "anchor_price": 1.0,
                "horizon_minutes": 60,
                "horizon_end_ms": timestamp + 60 * 60_000,
                "upper_barrier_price": 1.02,
                "lower_barrier_price": 0.98,
                "max_price": 1.12,
                "min_price": 0.97,
                "horizon_close_price": 1.04,
                "max_return": 0.12,
                "min_return": -0.03,
                "horizon_close_return": 0.04,
                "label": "UP_02",
                "touch_timestamp_ms": timestamp + 30 * 60_000,
                "touch_price": 1.02,
                "status": "FINAL",
                "reason": "upper_barrier_touched_first_by_candle_high",
            }
        ],
        columns=ANCHOR_COLUMNS,
    )


def test_sync_materializes_partitioned_targets(tmp_path: Path) -> None:
    anchor_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    anchor_store.upsert(_anchor_frame())
    target_store = EnvelopeTargetStore(tmp_path / "future_envelopes.parquet")

    result = sync_envelope_targets_from_anchors(anchor_store, target_store)

    assert result.stats.total_rows == 1
    key = HorizonPartitionKey(60, "2025-05")
    assert target_store.has_partition(key)
    target = target_store.load_partition(key).iloc[0]
    assert float(target["future_max_return"]) == 0.12
    assert float(target["future_min_return"]) == -0.03
    assert float(target["future_close_return"]) == 0.04
    assert bool(target["hit_up_02"]) is True
    assert bool(target["hit_down_02"]) is True


def test_sync_includes_complete_ambiguous_anchor_for_excursion_model(tmp_path: Path) -> None:
    anchors = _anchor_frame()
    anchors.loc[0, "status"] = "EXCLUDED"
    anchors.loc[0, "label"] = "AMBIGUOUS"
    anchors.loc[0, "reason"] = "both_barriers_touched_in_same_candle"
    anchor_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    anchor_store.upsert(anchors)
    target_store = EnvelopeTargetStore(tmp_path / "future_envelopes.parquet")

    result = sync_envelope_targets_from_anchors(anchor_store, target_store)

    assert result.stats.total_rows == 1


def test_sync_ignores_pending_anchor(tmp_path: Path) -> None:
    anchors = _anchor_frame()
    anchors.loc[0, "status"] = "PENDING"
    anchors.loc[0, "reason"] = "horizon_not_mature"
    anchor_store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    anchor_store.upsert(anchors)
    target_store = EnvelopeTargetStore(tmp_path / "future_envelopes.parquet")

    result = sync_envelope_targets_from_anchors(anchor_store, target_store)

    assert result.stats.total_rows == 0
