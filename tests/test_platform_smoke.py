from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI

from xasp.platform_api import create_app
from xasp.platform_runtime import RuntimeConfig, RuntimePaths
from xasp.platform_runtime_v2 import RealDataPlatformV2
from xasp.prediction_ledger import PredictionRecord


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
        if route.path == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_platform_wires_routes_without_network_or_market_fabrication(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))
    route_paths = {route.path for route in app.routes}

    assert platform.status.state == "WAIT"
    assert platform.status.reason == "both_model_evidence_gates_pending"
    assert platform.envelope.paths.targets == tmp_path / "data" / "future_envelopes.parquet"
    assert platform.envelope.paths.model == tmp_path / "models" / "envelope_champion.joblib"
    assert platform.envelope.paths.report == tmp_path / "reports" / "envelope_training.json"
    assert platform.envelope.paths.predictions == tmp_path / "data" / "envelope_predictions.parquet"
    assert platform.envelope.bundle is None
    assert "/api/status" in route_paths
    assert "/api/health" in route_paths
    assert "/api/models" in route_paths
    assert "/api/models/first-touch/latest" in route_paths
    assert "/api/models/adaptive-shock/latest" in route_paths
    assert "/api/reports/training/first-touch" in route_paths
    assert "/api/reports/training/adaptive-shock" in route_paths
    assert "/api/predictions/latest" in route_paths
    assert "/api/envelope/latest" in route_paths
    assert "/api/ledger" in route_paths
    assert "/api/run-cycle" in route_paths


def test_invalidated_first_touch_rows_are_hidden_from_public_ledger(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))
    platform.ledger.append(
        [
            PredictionRecord(
                created_at_ms=2,
                anchor_timestamp_ms=1,
                anchor_price=1.0,
                horizon_minutes=15,
                model_version="invalidated-model",
                dataset_id="test",
                feature_schema_version="test",
                p_up_10=0.0,
                p_down_10=0.0,
                p_no_event=1.0,
            )
        ]
    )
    app = create_app(platform, web_root=Path("."))

    assert _endpoint(app, "/api/ledger")() == []
    assert _endpoint(app, "/api/models/first-touch/latest")() == []


def test_first_touch_report_marks_legacy_gate_output_as_stale(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.reports.parent.mkdir(parents=True, exist_ok=True)
    paths.reports.write_text(
        json.dumps(
            {
                "15": {
                    "status": "RESEARCH_ONLY",
                    "reason": "empirical_85pct_gate_passed_not_trading_promoted",
                    "row_count": 100,
                    "metrics": {"per_class": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))

    payload = _endpoint(app, "/api/reports/training/first-touch")()

    assert payload["_meta"]["status"] == "STALE"
    assert payload["_meta"]["is_current"] is False
    assert payload["_meta"]["report_gate_methodology_versions"] == []


def test_windows_launcher_is_pinned_to_requested_port() -> None:
    launcher = Path("START_XASP.bat").read_text(encoding="utf-8")
    assert 'set "PORT=8654"' in launcher
    assert "pytest" in launcher
    assert "compileall" in launcher
    assert "xasp.platform_api" in launcher
