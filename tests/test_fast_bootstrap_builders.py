from __future__ import annotations

from pathlib import Path

import pandas as pd

from xasp.anchor_dataset import (
    AnchorDatasetConfig,
    AnchorDatasetStore,
    update_anchor_dataset_from_candles,
)
from xasp.dataset_state import DatasetStateStore
from xasp.future_envelope import build_future_envelope_targets
from xasp.labeling import CandlePoint

MINUTE = 60_000


def _candles(rows: int) -> list[CandlePoint]:
    output: list[CandlePoint] = []
    for index in range(rows):
        close = 100.0
        high = 111.0 if index == 10 else 100.0
        low = 89.0 if index == 80 else 100.0
        output.append(
            CandlePoint(
                timestamp_ms=index * MINUTE,
                open=100.0,
                high=high,
                low=low,
                close=close,
            )
        )
    return output


def test_vectorized_first_touch_builder_emits_all_real_anchors(tmp_path: Path) -> None:
    candles = _candles(121)
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")
    config = AnchorDatasetConfig(horizons_minutes=(15, 60))

    frame = update_anchor_dataset_from_candles(candles, store, state_store, config)

    assert len(frame) == len(candles) * 2
    first = frame[
        (frame["anchor_timestamp_ms"] == 0) & (frame["horizon_minutes"] == 15)
    ].iloc[0]
    assert first["label"] == "UP_10"
    assert first["status"] == "FINAL"
    pending = frame[
        (frame["anchor_timestamp_ms"] == 120 * MINUTE)
        & (frame["horizon_minutes"] == 60)
    ].iloc[0]
    assert pending["status"] == "PENDING"


def test_vectorized_first_touch_builder_excludes_gapped_paths(tmp_path: Path) -> None:
    candles = [candle for index, candle in enumerate(_candles(70)) if index != 30]
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state_store = DatasetStateStore(tmp_path / "state.json")

    frame = update_anchor_dataset_from_candles(
        candles,
        store,
        state_store,
        AnchorDatasetConfig(horizons_minutes=(60,)),
    )

    anchor = frame[frame["anchor_timestamp_ms"] == 0].iloc[0]
    assert anchor["label"] == "INCOMPLETE"
    assert anchor["status"] == "EXCLUDED"


def test_vectorized_future_envelope_builder_uses_complete_windows_only() -> None:
    candles = _candles(121)
    prices = pd.DataFrame(
        {
            "timestamp_ms": [candle.timestamp_ms for candle in candles],
            "price": [candle.close for candle in candles],
            "high": [candle.high for candle in candles],
            "low": [candle.low for candle in candles],
        }
    )

    targets = build_future_envelope_targets(prices, horizons=(15, 60))

    assert len(targets[targets["horizon_minutes"] == 15]) == 121 - 15
    assert len(targets[targets["horizon_minutes"] == 60]) == 121 - 60
    first = targets[
        (targets["anchor_timestamp_ms"] == 0)
        & (targets["horizon_minutes"] == 15)
    ].iloc[0]
    assert first["future_max_price"] == 111.0
    assert first["minutes_to_max"] == 10
