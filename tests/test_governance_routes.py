from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

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


def _endpoint(app: FastAPI, path: str) -> Callable[..., Any]:
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_governance_routes_wait_without_reports(tmp_path: Path) -> None:
    platform = RealDataPlatformV2(_paths(tmp_path), RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))

    integrity = _endpoint(app, "/api/reports/data-integrity")()
    expansion = _endpoint(app, "/api/history-expansion")()
    summary = _endpoint(app, "/api/governance")()

    assert integrity["status"] == "WAIT"
    assert integrity["reason"] == "no_data_integrity_report"
    assert expansion["status"] == "IDLE"
    assert expansion["progress_fraction"] == 0.0
    assert summary["training_allowed_by_platform_policy"] is True
    assert summary["trading_promoted"] is False


def test_governance_routes_expose_integrity_and_expansion_progress(tmp_path: Path) -> None:
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
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))

    summary = _endpoint(app, "/api/governance")()

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
    app = create_app(
        RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1)),
        web_root=Path("."),
    )

    expansion = _endpoint(app, "/api/history-expansion")()

    assert expansion["status"] == "READY"
    assert expansion["progress_fraction"] == 1.0
    assert expansion["completed"] is True
