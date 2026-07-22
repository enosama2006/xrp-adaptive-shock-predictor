from __future__ import annotations

import json
from pathlib import Path

from xasp.pipeline import PipelineProgress
from xasp.platform_runtime import RealDataPlatform, RuntimeConfig, RuntimePaths


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        prices=tmp_path / "prices.parquet",
        anchors=tmp_path / "anchors.parquet",
        features=tmp_path / "features.parquet",
        state=tmp_path / "state.json",
        models=tmp_path / "champion.joblib",
        reports=tmp_path / "training.json",
        feature_diagnostics=tmp_path / "feature_diagnostics.json",
        ledger=tmp_path / "predictions.parquet",
        status=tmp_path / "platform_status.json",
    )


def test_old_status_file_is_migrated_with_lifecycle_defaults(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.status.write_text(
        json.dumps(
            {
                "updated_at_ms": 1,
                "state": "WAIT",
                "reason": "legacy_status",
                "price_rows": 25,
                "anchor_rows": 40,
                "final_rows": 10,
                "pending_rows": 30,
                "model_available": False,
                "model_version": None,
                "last_prediction_ms": None,
                "last_training_final_rows": 0,
                "data_start_ms": 0,
                "data_end_ms": 24 * 60_000,
            }
        ),
        encoding="utf-8",
    )

    platform = RealDataPlatform(paths, RuntimeConfig(bootstrap_start_ms=0))

    assert platform.status.reason == "legacy_status"
    assert platform.status.price_rows == 25
    assert platform.status.lifecycle_stage == "IDLE"
    assert platform.status.lifecycle_progress == 0.0
    assert platform.status.checkpoint_writes == 0


def test_pipeline_progress_is_persisted_as_bootstrap_lifecycle(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    platform = RealDataPlatform(paths, RuntimeConfig(bootstrap_start_ms=0))
    platform._active_collection_stage = "BOOTSTRAP_HISTORY"

    platform._on_pipeline_progress(
        PipelineProgress(
            stage="COLLECT_HISTORY",
            requested_start_ms=0,
            requested_end_ms=99 * 60_000,
            expected_rows=100,
            processed_rows=40,
            total_price_rows=40,
            checkpoint_writes=2,
            current_watermark_ms=39 * 60_000,
            progress_fraction=0.4,
        )
    )

    saved = json.loads(paths.status.read_text(encoding="utf-8"))
    assert saved["lifecycle_stage"] == "BOOTSTRAP_HISTORY"
    assert saved["lifecycle_progress"] == 0.4
    assert saved["processed_rows"] == 40
    assert saved["expected_rows"] == 100
    assert saved["checkpoint_writes"] == 2
    assert saved["current_watermark_ms"] == 39 * 60_000
