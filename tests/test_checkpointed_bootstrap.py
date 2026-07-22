from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from xasp.pipeline import IncrementalResearchPipeline, PipelineConfig, PipelinePaths

MINUTE = 60_000


def _record(timestamp_ms: int, price: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        event_time_ms=timestamp_ms,
        payload={
            "close_time_ms": timestamp_ms,
            "open": str(price),
            "high": str(price),
            "low": str(price),
            "close": str(price),
            "volume": "100",
            "quote_volume": "100",
            "trade_count": 10,
            "taker_buy_base": "50",
            "taker_buy_quote": "50",
        },
    )


class FailingClient:
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ):
        del symbol, interval, start_time_ms, end_time_ms, limit
        for minute in range(7):
            yield _record(minute * MINUTE, 1.0 + minute / 1000)
        raise RuntimeError("simulated connection loss")


class RangeClient:
    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ):
        del symbol, interval, limit
        first = ((start_time_ms + MINUTE - 1) // MINUTE) * MINUTE
        for timestamp in range(first, end_time_ms + 1, MINUTE):
            yield _record(timestamp, 1.0 + timestamp / MINUTE / 1000)


def _paths(tmp_path: Path) -> PipelinePaths:
    return PipelinePaths(
        prices=tmp_path / "prices.parquet",
        anchors=tmp_path / "anchors.parquet",
        state=tmp_path / "state.json",
    )


def test_interrupted_backfill_preserves_completed_checkpoints(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    pipeline = IncrementalResearchPipeline(
        paths,
        PipelineConfig(bootstrap_start_ms=0, checkpoint_rows=3),
        FailingClient(),
    )

    with pytest.raises(RuntimeError, match="simulated connection loss"):
        pipeline.run(10 * MINUTE)

    saved = pd.read_parquet(paths.prices)
    assert len(saved) == 6
    assert int(saved["timestamp_ms"].max()) == 5 * MINUTE
    assert paths.state.exists()


def test_restart_resumes_from_checkpoint_and_completes_range(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    interrupted = IncrementalResearchPipeline(
        paths,
        PipelineConfig(bootstrap_start_ms=0, checkpoint_rows=3),
        FailingClient(),
    )
    with pytest.raises(RuntimeError):
        interrupted.run(10 * MINUTE)

    progress_events = []
    resumed = IncrementalResearchPipeline(
        paths,
        PipelineConfig(bootstrap_start_ms=0, checkpoint_rows=2, overlap_minutes=2),
        RangeClient(),
    )
    result = resumed.run(10 * MINUTE, progress_callback=progress_events.append)

    saved = pd.read_parquet(paths.prices)
    assert result.total_price_rows == 11
    assert len(saved) == 11
    assert int(saved["timestamp_ms"].min()) == 0
    assert int(saved["timestamp_ms"].max()) == 10 * MINUTE
    assert result.requested_start_ms > 0
    assert result.checkpoint_writes >= 1
    assert progress_events[0].stage == "COLLECT_HISTORY"
    assert progress_events[-1].stage == "DATA_CHECKPOINTED"
    assert progress_events[-1].progress_fraction == 1.0
