from __future__ import annotations

from pathlib import Path

from xasp.platform_api import create_app
from xasp.platform_runtime import RuntimeConfig, RuntimePaths
from xasp.platform_runtime_v2 import RealDataPlatformV2


def test_platform_wires_routes_without_network_or_market_fabrication(tmp_path: Path) -> None:
    paths = RuntimePaths(
        prices=tmp_path / "data" / "prices.parquet",
        anchors=tmp_path / "data" / "anchors.parquet",
        features=tmp_path / "data" / "features.parquet",
        state=tmp_path / "data" / "state.json",
        models=tmp_path / "models" / "champion.joblib",
        reports=tmp_path / "reports" / "training.json",
        ledger=tmp_path / "data" / "predictions.parquet",
        status=tmp_path / "data" / "platform_status.json",
    )
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))
    route_paths = {route.path for route in app.routes}

    assert platform.status.state == "WAIT"
    assert platform.status.reason == "not_started"
    assert "/api/status" in route_paths
    assert "/api/health" in route_paths
    assert "/api/predictions/latest" in route_paths
    assert "/api/envelope/latest" in route_paths
    assert "/api/ledger" in route_paths
    assert "/api/run-cycle" in route_paths


def test_windows_launcher_is_pinned_to_requested_port() -> None:
    launcher = Path("START_XASP.bat").read_text(encoding="utf-8")
    assert 'set "PORT=8654"' in launcher
    assert "pytest" in launcher
    assert "compileall" in launcher
    assert "xasp.platform_api" in launcher
