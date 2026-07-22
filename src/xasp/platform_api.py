"""FastAPI service for the real-data XASP platform."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn

from .platform_runtime_v2 import RealDataPlatformV2, RuntimeConfig, RuntimePaths
from .production_report import ProductionReportPaths


def create_app(platform: RealDataPlatformV2, web_root: Path = Path(".")) -> FastAPI:
    app = FastAPI(title="XASP Real Data Platform", version="1.0.0")
    cycle_lock = Lock()

    @app.on_event("startup")
    async def start_worker() -> None:
        async def worker() -> None:
            while True:
                try:
                    if cycle_lock.acquire(blocking=False):
                        try:
                            await asyncio.to_thread(platform.run_cycle)
                        finally:
                            cycle_lock.release()
                except Exception as exc:
                    platform.status.state = "WAIT"
                    platform.status.reason = f"runtime_error:{type(exc).__name__}:{exc}"
                    platform._save_status()
                await asyncio.sleep(60)

        app.state.worker = asyncio.create_task(worker())

    @app.on_event("shutdown")
    async def stop_worker() -> None:
        task = getattr(app.state, "worker", None)
        if task is not None:
            task.cancel()

    def model_catalog() -> dict[str, Any]:
        shock_bundle = platform.envelope.bundle
        touch_bundle = platform._bundle
        return {
            "collection": {
                "symbol": platform.config.symbol,
                "sampling": "1-minute observed candles",
                "prediction_cadence_seconds": platform.config.prediction_cadence_ms // 1000,
                "bootstrap_start_ms": platform.config.bootstrap_start_ms,
                "retraining_policy": "daily after 5,760 newly finalized horizon rows",
                "source": "Binance public observed market data",
            },
            "adaptive_shock": {
                "display_name": "Adaptive Shock Magnitude Model",
                "technical_name": "future-excursion quantile regression",
                "purpose": "Estimate upside and downside excursion ranges for 15/30/45/60 minutes.",
                "available": shock_bundle is not None,
                "model_version": None if shock_bundle is None else shock_bundle.get("model_version"),
                "trained_at_ms": None if shock_bundle is None else shock_bundle.get("trained_at_ms"),
                "training_rows": None if shock_bundle is None else shock_bundle.get("training_final_rows"),
                "gate": "85% empirical interval coverage on untouched temporal test data",
                "endpoint": "/api/models/adaptive-shock/latest",
            },
            "first_touch_10": {
                "display_name": "±10% First-Touch Model",
                "technical_name": "calibrated multiclass first-touch classifier",
                "purpose": "Estimate whether +10%, -10%, or neither is reached first within each horizon.",
                "available": touch_bundle is not None,
                "model_version": None if touch_bundle is None else touch_bundle.get("model_version"),
                "trained_at_ms": None if touch_bundle is None else touch_bundle.get("trained_at_ms"),
                "training_rows": None if touch_bundle is None else touch_bundle.get("training_final_rows"),
                "gate": "85% empirical precision for high-confidence predictions on untouched temporal test data",
                "endpoint": "/api/models/first-touch/latest",
            },
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        paths = platform.paths
        report_paths = ProductionReportPaths()
        storage = {
            "prices": paths.prices.exists(),
            "anchors": paths.anchors.exists(),
            "features": paths.features.exists(),
            "state": paths.state.exists(),
            "first_touch_model": paths.models.exists(),
            "first_touch_report": paths.reports.exists(),
            "prediction_ledger": paths.ledger.exists(),
            "shock_targets": platform.envelope.paths.targets.exists(),
            "shock_model": platform.envelope.paths.model.exists(),
            "shock_report": platform.envelope.paths.report.exists(),
            "production_report": report_paths.latest_json.exists(),
            "production_report_history": report_paths.history_jsonl.exists(),
        }
        return {
            "service": "UP",
            "runtime_state": platform.status.state,
            "runtime_reason": platform.status.reason,
            "data_available": storage["prices"] and storage["features"],
            "first_touch_model_available": platform._bundle is not None,
            "adaptive_shock_model_available": platform.envelope.bundle is not None,
            "storage": storage,
            "ready_for_research_predictions": (
                platform._bundle is not None and platform.envelope.bundle is not None
            ),
            "ready_for_trading": False,
        }

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        payload = asdict(platform.status)
        payload["adaptive_shock_model_available"] = platform.envelope.bundle is not None
        payload["first_touch_model_available"] = platform._bundle is not None
        payload["required_empirical_confidence"] = 0.85
        payload["confidence_note"] = (
            "85% is an empirical out-of-sample gate, not a guarantee of future correctness"
        )
        return payload

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        return model_catalog()

    @app.get("/api/models/first-touch/latest")
    @app.get("/api/predictions/latest")
    def latest_first_touch() -> list[dict[str, Any]]:
        frame = platform.ledger.load()
        if frame.empty:
            return []
        latest_anchor = int(frame["anchor_timestamp_ms"].max())
        subset = frame[frame["anchor_timestamp_ms"] == latest_anchor]
        return subset.where(subset.notna(), None).to_dict(orient="records")

    @app.get("/api/models/adaptive-shock/latest")
    @app.get("/api/envelope/latest")
    def latest_adaptive_shock() -> list[dict[str, Any]]:
        return platform.envelope.latest_predictions()

    @app.get("/api/ledger")
    def ledger(limit: int = 100) -> list[dict[str, Any]]:
        frame = (
            platform.ledger.load()
            .sort_values("created_at_ms", ascending=False)
            .head(max(1, min(limit, 1000)))
        )
        return frame.where(frame.notna(), None).to_dict(orient="records")

    @app.get("/api/reports/production")
    def production_report() -> dict[str, Any]:
        path = ProductionReportPaths().latest_json
        if not path.exists():
            return platform.generate_production_report()
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/reports/production/refresh")
    def refresh_production_report() -> dict[str, Any]:
        if not cycle_lock.acquire(blocking=False):
            return {"state": "BUSY"}
        try:
            return platform.generate_production_report()
        finally:
            cycle_lock.release()

    @app.post("/api/run-cycle")
    def run_cycle() -> dict[str, Any]:
        if not cycle_lock.acquire(blocking=False):
            return {"state": "BUSY"}
        try:
            return platform.run_cycle()
        finally:
            cycle_lock.release()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/app.js")
    def javascript() -> FileResponse:
        return FileResponse(web_root / "app.js", media_type="application/javascript")

    @app.get("/styles.css")
    def stylesheet() -> FileResponse:
        return FileResponse(web_root / "styles.css", media_type="text/css")

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run XASP with real Binance data only")
    parser.add_argument("--bootstrap-start-ms", required=True, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8654, type=int)
    parser.add_argument("--minimum-final-rows", default=2_000, type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    platform = RealDataPlatformV2(
        RuntimePaths(),
        RuntimeConfig(
            bootstrap_start_ms=args.bootstrap_start_ms,
            minimum_final_rows_per_horizon=args.minimum_final_rows,
            retrain_after_new_final_rows=5_760,
        ),
    )
    uvicorn.run(create_app(platform), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
