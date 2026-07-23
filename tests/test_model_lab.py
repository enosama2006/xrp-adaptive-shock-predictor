from __future__ import annotations

from pathlib import Path

import pandas as pd

from xasp.model_lab import MODEL_A_KEY, MODEL_B_KEY, ModelLabService
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
        feature_diagnostics=tmp_path / "reports" / "feature_diagnostics.json",
        ledger=tmp_path / "data" / "predictions.parquet",
        status=tmp_path / "data" / "platform_status.json",
    )


def test_lab_routes_are_published_and_static_page_is_mounted(tmp_path: Path) -> None:
    platform = RealDataPlatformV2(_paths(tmp_path), RuntimeConfig(bootstrap_start_ms=1))
    app = create_app(platform, web_root=Path("."))
    openapi_paths = set(app.openapi()["paths"])
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert "/api/lab/overview" in openapi_paths
    assert "/api/lab/current-inputs" in openapi_paths
    assert "/api/lab/predict" in openapi_paths
    assert "/api/model-lab" in route_paths
    assert "/api/lab.js" in route_paths
    assert "/api/lab.css" in route_paths


def test_lab_is_fail_closed_without_governed_model_bundle(tmp_path: Path) -> None:
    platform = RealDataPlatformV2(_paths(tmp_path), RuntimeConfig(bootstrap_start_ms=1))
    service = ModelLabService(platform)

    overview = service.overview_payload()
    model_a = overview["models"][MODEL_A_KEY]
    model_b = overview["models"][MODEL_B_KEY]
    result = service.predict_payload(
        model_key=MODEL_A_KEY,
        horizon_minutes=60,
        input_source="manual",
        anchor_price=1.0,
        feature_values={},
    )

    assert model_a["state"] == "WAIT"
    assert model_b["state"] == "WAIT"
    assert model_a["promoted_for_trading"] is False
    assert model_b["promoted_for_trading"] is False
    assert result["status"] == "WAIT"
    assert result["reason"] == "no_governed_model_bundle_available"
    assert result["persisted"] is False


def test_current_inputs_use_latest_completed_feature_row_and_registry(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.features.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp_ms": [60_000, 120_000, 180_000],
            "price": [0.50, 0.55, 0.60],
            "return_1m": [0.0, 0.10, 0.0909],
            "volatility_15m": [0.01, 0.02, 0.03],
            "unknown_numeric": [1.0, 2.0, 3.0],
        }
    ).to_parquet(paths.features, index=False)
    platform = RealDataPlatformV2(paths, RuntimeConfig(bootstrap_start_ms=1))

    payload = ModelLabService(platform).current_inputs_payload()
    names = {item["name"] for item in payload["features"]}

    assert payload["status"] == "READY"
    assert payload["timestamp_ms"] == 180_000
    assert payload["anchor_price"] == 0.60
    assert payload["values"]["return_1m"] == 0.0909
    assert "return_1m" in names
    assert "volatility_15m" in names
    assert "unknown_numeric" not in names
