from __future__ import annotations

import json
from pathlib import Path

from xasp.governance_routes import GovernanceEvidenceReader
from xasp.platform_api import create_app
from xasp.platform_runtime import RuntimeConfig, RuntimePaths
from xasp.platform_runtime_v2 import RealDataPlatformV2


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        prices=tmp_path / "data" / "prices.parquet",
        anchors=tmp_path / "data" / "anchors.parquet",
        features=tmp_path / "data" / "features.parquet",
        state=tmp_path / "data" / "state.json",
        models=tmp_path / "models" / "champion.joblib",
        reports=tmp_path / "reports" / "training.json",
        ledger=tmp_path / "data" / "predictions.parquet",
        status=tmp_path / "data" / "platform_status.json",
    )


def _reader(paths: RuntimePaths) -> GovernanceEvidenceReader:
    return GovernanceEvidenceReader(
        integrity_path=paths.reports.parent / "data_integrity.json",
        expansion_path=paths.state.parent / "history_expansion_state.json",
    )


def test_governance_waits_without_reports_and_routes_are_published(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    reader = _reader(paths)
    app = create_app(
        RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1)),
        web_root=Path("."),
    )

    integrity = reader.integrity_payload()
    expansion = reader.expansion_payload()
    summary = reader.summary_payload()
    openapi_paths = set(app.openapi()["paths"])

    assert integrity["status"] == "WAIT"
    assert integrity["reason"] == "no_data_integrity_report"
    assert expansion["status"] == "IDLE"
    assert expansion["progress_fraction"] == 0.0
    assert summary["training_allowed_by_platform_policy"] is True
    assert summary["trading_promoted"] is False
    assert "/api/reports/data-integrity" in openapi_paths
    assert "/api/history-expansion" in openapi_paths
    assert "/api/governance" in openapi_paths


def test_governance_exposes_integrity_and_expansion_progress(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.reports.parent.mkdir(parents=True, exist_ok=True)
    (paths.reports.parent / "data_integrity.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "reason": "price_integrity_and_coverage_passed",
                "coverage_ratio": 0.999,
                "missing_minutes": 7,
                "dataset_fingerprint_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    (paths.state.parent / "history_expansion_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "symbol": "XRPUSDT",
                "target_start_ms": 60_000,
                "target_end_ms": 600_000,
                "next_open_time_ms": 300_000,
                "accepted_rows": 4,
                "checkpoint_writes": 2,
                "completed": False,
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    summary = _reader(paths).summary_payload()

    assert summary["data_integrity"]["status"] == "PASS"
    assert summary["data_integrity"]["dataset_fingerprint_sha256"] == "a" * 64
    assert summary["history_expansion"]["status"] == "WAIT"
    assert summary["history_expansion"]["reason"] == "history_expansion_checkpointed_incomplete"
    assert 0.0 < summary["history_expansion"]["progress_fraction"] < 1.0
    assert summary["history_expansion"]["accepted_rows"] == 4


def test_completed_history_expansion_reports_full_progress(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    (paths.state.parent / "history_expansion_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "symbol": "XRPUSDT",
                "target_start_ms": 60_000,
                "target_end_ms": 600_000,
                "next_open_time_ms": 600_000,
                "accepted_rows": 10,
                "checkpoint_writes": 1,
                "completed": True,
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    expansion = _reader(paths).expansion_payload()

    assert expansion["status"] == "READY"
    assert expansion["progress_fraction"] == 1.0
    assert expansion["completed"] is True
