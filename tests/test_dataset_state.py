from pathlib import Path

import pytest

from xasp.dataset_state import DatasetState, DatasetStateStore


def test_missing_state_starts_clean(tmp_path: Path) -> None:
    store = DatasetStateStore(tmp_path / "state.json")
    state = store.load()
    assert state.raw_watermarks_ms == {}
    assert state.feature_watermark_ms is None


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = DatasetStateStore(path)
    state = DatasetState(dataset_id="research-v1")
    state.advance_raw_watermark("spot:XRPUSDT:trades", 123_000)
    state.feature_watermark_ms = 120_000
    state.pending_label_count = 15
    store.save(state)

    restored = store.load()
    assert restored.dataset_id == "research-v1"
    assert restored.raw_watermarks_ms["spot:XRPUSDT:trades"] == 123_000
    assert restored.feature_watermark_ms == 120_000
    assert restored.pending_label_count == 15


def test_watermark_cannot_move_backwards() -> None:
    state = DatasetState()
    state.advance_raw_watermark("spot:XRPUSDT:trades", 200)
    with pytest.raises(ValueError, match="cannot move backwards"):
        state.advance_raw_watermark("spot:XRPUSDT:trades", 199)


def test_invalid_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        DatasetStateStore(path).load()
