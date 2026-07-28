from pathlib import Path
from types import SimpleNamespace

from xasp.pipeline import (
    IncrementalResearchPipeline,
    PipelineConfig,
    PipelinePaths,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ):
        self.calls.append((start_time_ms, end_time_ms))
        minute = 60_000
        first_open = (start_time_ms // minute) * minute
        for open_time in range(first_open, end_time_ms + 1, minute):
            close_time = open_time + minute - 1
            price = 1.0 if open_time < 15 * minute else 1.11
            yield SimpleNamespace(
                event_time_ms=close_time,
                payload={
                    "open_time_ms": open_time,
                    "close_time_ms": close_time,
                    "open": str(price),
                    "high": str(price),
                    "low": str(price),
                    "close": str(price),
                    "volume": "100",
                },
            )


def test_pipeline_resumes_missing_tail_without_full_rebuild(tmp_path: Path) -> None:
    paths = PipelinePaths(
        prices=tmp_path / "prices.parquet",
        anchors=tmp_path / "anchors.parquet",
        state=tmp_path / "state.json",
    )
    client = FakeClient()
    pipeline = IncrementalResearchPipeline(
        paths,
        PipelineConfig(bootstrap_start_ms=0, overlap_minutes=2),
        client,
    )

    first = pipeline.run(20 * 60_000)
    second = pipeline.run(25 * 60_000)

    assert first.total_price_rows == 20
    assert second.total_price_rows == 25
    assert second.requested_start_ms > 0
    assert second.requested_start_ms < 20 * 60_000
    assert second.total_price_rows < first.total_price_rows + second.fetched_rows


def test_pipeline_updates_final_and_pending_counts(tmp_path: Path) -> None:
    paths = PipelinePaths(
        prices=tmp_path / "prices.parquet",
        anchors=tmp_path / "anchors.parquet",
        state=tmp_path / "state.json",
    )
    pipeline = IncrementalResearchPipeline(
        paths,
        PipelineConfig(bootstrap_start_ms=0),
        FakeClient(),
    )

    result = pipeline.run(70 * 60_000)

    assert result.anchor_rows > 0
    assert result.finalized_labels > 0
    assert result.pending_labels > 0
