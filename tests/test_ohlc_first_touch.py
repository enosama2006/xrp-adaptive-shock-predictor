from __future__ import annotations

from pathlib import Path

from xasp.anchor_dataset import (
    AnchorDatasetConfig,
    AnchorDatasetStore,
    update_anchor_dataset_from_candles,
)
from xasp.dataset_state import DatasetStateStore
from xasp.labeling import (
    BarrierConfig,
    BarrierLabel,
    CandlePoint,
    PricePoint,
    label_first_touch_candles,
)

MINUTE = 60_000


def candle(minute: int, *, close: float = 100.0, high: float = 100.0, low: float = 100.0) -> CandlePoint:
    return CandlePoint(
        timestamp_ms=minute * MINUTE,
        open=100.0,
        high=high,
        low=low,
        close=close,
    )


def test_intraminute_upper_touch_is_not_missed_when_close_reverts() -> None:
    path = [candle(i) for i in range(1, 61)]
    path[9] = candle(10, close=101.0, high=111.0, low=99.0)

    result = label_first_touch_candles(PricePoint(0, 100.0), path)

    assert result.label == BarrierLabel.UP_10
    assert result.touch_timestamp_ms == 10 * MINUTE
    assert result.touch_price == 110.0


def test_same_candle_dual_touch_is_ambiguous() -> None:
    path = [candle(i) for i in range(1, 61)]
    path[4] = candle(5, close=100.0, high=111.0, low=89.0)

    result = label_first_touch_candles(PricePoint(0, 100.0), path)

    assert result.label == BarrierLabel.AMBIGUOUS
    assert result.reason == "both_barriers_touched_within_same_candle"


def test_missing_internal_minute_forces_incomplete() -> None:
    path = [candle(i) for i in range(1, 61) if i != 30]

    result = label_first_touch_candles(PricePoint(0, 100.0), path)

    assert result.label == BarrierLabel.INCOMPLETE
    assert result.reason == "incomplete_or_gapped_candle_path"


def test_anchor_dataset_uses_candle_high_and_excludes_ambiguous(tmp_path: Path) -> None:
    candles = [candle(i, close=100.0) for i in range(0, 21)]
    candles[10] = candle(10, close=100.0, high=111.0, low=100.0)
    store = AnchorDatasetStore(tmp_path / "anchors.parquet")
    state = DatasetStateStore(tmp_path / "state.json")

    frame = update_anchor_dataset_from_candles(
        candles,
        store,
        state,
        AnchorDatasetConfig(horizons_minutes=(15,), cadence_ms=MINUTE),
    )

    anchor = frame[frame["anchor_timestamp_ms"] == 0].iloc[0]
    assert anchor["label"] == "UP_10"
    assert anchor["status"] == "FINAL"
    assert anchor["max_price"] == 111.0


def test_candle_horizon_must_align_to_cadence() -> None:
    path = [candle(1)]
    try:
        label_first_touch_candles(
            PricePoint(0, 100.0),
            path,
            BarrierConfig(horizon_ms=90_000),
        )
    except ValueError as exc:
        assert "divisible" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected cadence validation failure")
