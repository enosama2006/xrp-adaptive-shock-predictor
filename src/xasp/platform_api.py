"""FastAPI service for the real-data XASP platform."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import json
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn

from .baseline import FIRST_TOUCH_GATE_VERSION
from .platform_runtime_v2 import RealDataPlatformV2, RuntimeConfig, RuntimePaths
from .production_report import ProductionReportPaths

HORIZON_KEYS = ("15", "30", "45", "60")


def create_app(platform: RealDataPlatformV2, web_root: Path = Path(".")) -> FastAPI:
    cycle_lock = Lock()

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
                platform._set_lifecycle(
                    "ERROR",
                    progress=0.0,
                    message=f"{type(exc).__name__}:{exc}",
                )
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(worker(), name="xasp-real-data-worker")
        app.state.worker = task
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="XASP Real Data Platform",
        version="1.2.0",
        lifespan=lifespan,
    )

    def first_touch_training_report_payload() -> dict[str, Any]:
        path = platform.paths.reports
        if not path.exists():
            return {
                "_meta": {
                    "status": "WAIT",
                    "reason": "no_first_touch_training_report",
                    "is_current": False,
                    "current_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
                    "report_gate_methodology_versions": [],
                    "model_available": platform._bundle is not None,
                }
            }

        raw = json.loads(path.read_text(encoding="utf-8"))
        horizons = {
            key: value
            for key, value in raw.items()
            if key in HORIZON_KEYS and isinstance(value, dict)
        }
        versions = sorted(
            {
                str(version)
                for report in horizons.values()
                if (version := report.get("metrics", {}).get("gate_methodology_version"))
                is not None
            }
        )
        current = bool(horizons) and versions == [FIRST_TOUCH_GATE_VERSION]
        statuses = sorted({str(report.get("status", "WAIT")) for report in horizons.values()})
        reasons = {
            key: str(report.get("reason", "unknown")) for key, report in horizons.items()
        }
        walk_forward = {
            key: report.get("metrics", {}).get("walk_forward_support_audit")
            for key, report in horizons.items()
            if report.get("metrics", {}).get("walk_forward_support_audit") is not None
        }
        meta = {
            "status": "CURRENT" if current else "STALE" if horizons else "WAIT",
            "reason": (
                "report_matches_current_walk_forward_directional_gate"
                if current
                else "report_was_generated_by_an_older_gate_or_training_is_still_running"
            ),
            "is_current": current,
            "current_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "report_gate_methodology_versions": versions,
            "horizon_statuses": statuses,
            "horizon_reasons": reasons,
            "walk_forward_support_by_horizon": walk_forward,
            "report_updated_at_ms": int(path.stat().st_mtime * 1000),
            "model_available": platform._bundle is not None,
            "runtime_state": platform.status.state,
            "runtime_reason": platform.status.reason,
        }
        return {"_meta": meta, **horizons}

    def active_first_touch_ledger() -> Any:
        if platform._bundle is None:
            return platform.ledger.load().iloc[0:0]
        frame = platform.ledger.load()
        if frame.empty:
            return frame
        active_version = str(platform._bundle["model_version"])
        return frame[frame["model_version"] == active_version].copy()

    def active_envelope_predictions() -> Any:
        if platform.envelope.bundle is None or not platform.envelope.paths.predictions.exists():
            return []
        frame = platform._active_envelope_predictions()
        if frame.empty:
            return []
        latest_anchor = int(frame["anchor_timestamp_ms"].max())
        subset = frame[frame["anchor_timestamp_ms"] == latest_anchor]
        return subset.where(subset.notna(), None).to_dict(orient="records")

    def model_catalog() -> dict[str, Any]:
        shock_bundle = platform.envelope.bundle
        touch_bundle = platform._bundle
        touch_report = first_touch_training_report_payload()
        touch_meta = touch_report.get("_meta", {})
        touch_training_rows = None
        horizon_rows = [
            int(report.get("row_count", 0))
            for key, report in touch_report.items()
            if key in HORIZON_KEYS and isinstance(report, dict)
        ]
        if horizon_rows:
            touch_training_rows = max(horizon_rows)
        if touch_bundle is not None:
            touch_training_rows = int(touch_bundle.get("training_final_rows", 0))
        price_stats = platform.price_store.stats()

        return {
            "collection": {
                "symbol": platform.config.symbol,
                "sampling": "1-minute observed completed candles",
                "prediction_cadence_seconds": platform.config.prediction_cadence_ms // 1000,
                "bootstrap_start_ms": platform.config.bootstrap_start_ms,
                "checkpoint_rows": platform.config.checkpoint_rows,
                "price_rows": price_stats.total_rows,
                "price_partition_count": price_stats.partition_count,
                "price_partition_granularity": "UTC_MONTH",
                "retraining_policy": "daily after 5,760 newly finalized horizon rows",
                "source": "Binance public observed market data",
            },
            "adaptive_shock": {
                "display_name": "Adaptive Shock Magnitude Model",
                "technical_name": "future-excursion quantile regression",
                "purpose": (
                    "Estimate upside and downside excursion ranges for "
                    "15/30/45/60 minutes."
                ),
                "available": shock_bundle is not None,
                "availability_reason": (
                    "historical_interval_gate_passed"
                    if shock_bundle is not None
                    else "no_valid_adaptive_shock_bundle"
                ),
                "model_version": (
                    None if shock_bundle is None else shock_bundle.get("model_version")
                ),
                "trained_at_ms": (
                    None if shock_bundle is None else shock_bundle.get("trained_at_ms")
                ),
                "training_rows": (
                    None if shock_bundle is None else shock_bundle.get("training_final_rows")
                ),
                "gate": "85% empirical marginal interval coverage on untouched temporal test data",
                "endpoint": "/api/models/adaptive-shock/latest",
                "training_report_endpoint": "/api/reports/training/adaptive-shock",
            },
            "first_touch_10": {
                "display_name": "±10% First-Touch Model",
                "technical_name": "calibrated multiclass first-touch classifier",
                "purpose": (
                    "Estimate whether +10%, -10%, or neither is reached first "
                    "within each horizon."
                ),
                "available": touch_bundle is not None,
                "availability_reason": (
                    "walk_forward_directional_event_gate_passed"
                    if touch_bundle is not None
                    else platform.status.reason
                ),
                "model_version": (
                    None if touch_bundle is None else touch_bundle.get("model_version")
                ),
                "trained_at_ms": (
                    None if touch_bundle is None else touch_bundle.get("trained_at_ms")
                ),
                "training_rows": touch_training_rows,
                "gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
                "training_report_current": bool(touch_meta.get("is_current", False)),
                "training_report_status": touch_meta.get("status", "WAIT"),
                "gate": (
                    "Multiple purged walk-forward test periods must contain enough UP_10 and "
                    "DOWN_10 events; the latest untouched temporal test must then achieve 85% "
                    "empirical precision for high-confidence directional predictions. "
                    "NO_EVENT accuracy cannot pass the gate."
                ),
                "endpoint": "/api/models/first-touch/latest",
                "training_report_endpoint": "/api/reports/training/first-touch",
            },
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        paths = platform.paths
        report_paths = ProductionReportPaths()
        price_stats = platform.price_store.stats()
        storage = {
            "prices": platform.price_store.exists,
            "price_partitions": price_stats.partition_count > 0,
            "legacy_price_file": paths.prices.exists(),
            "anchors": paths.anchors.exists(),
            "features": paths.features.exists(),
            "state": paths.state.exists(),
            "first_touch_model_file": paths.models.exists(),
            "first_touch_report": paths.reports.exists(),
            "prediction_ledger": paths.ledger.exists(),
            "shock_targets": platform.envelope.paths.targets.exists(),
            "shock_model_file": platform.envelope.paths.model.exists(),
            "shock_report": platform.envelope.paths.report.exists(),
            "production_report": report_paths.latest_json.exists(),
            "production_report_history": report_paths.history_jsonl.exists(),
        }
        touch_ready = platform._bundle is not None
        shock_ready = platform.envelope.bundle is not None
        return {
            "service": "UP",
            "runtime_state": platform.status.state,
            "runtime_reason": platform.status.reason,
            "lifecycle_stage": platform.status.lifecycle_stage,
            "lifecycle_progress": platform.status.lifecycle_progress,
            "lifecycle_message": platform.status.lifecycle_message,
            "data_available": storage["prices"] and storage["features"],
            "price_store": asdict(price_stats),
            "first_touch_model_available": touch_ready,
            "adaptive_shock_model_available": shock_ready,
            "first_touch_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "storage": storage,
            "ready_for_first_touch_research": touch_ready,
            "ready_for_adaptive_shock_research": shock_ready,
            "ready_for_any_research_prediction": touch_ready or shock_ready,
            "ready_for_all_research_predictions": touch_ready and shock_ready,
            "ready_for_trading": False,
        }

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        payload = asdict(platform.status)
        payload["adaptive_shock_model_available"] = platform.envelope.bundle is not None
        payload["first_touch_model_available"] = platform._bundle is not None
        payload["first_touch_gate_methodology_version"] = FIRST_TOUCH_GATE_VERSION
        payload["required_empirical_confidence"] = 0.85
        payload["confidence_note"] = (
            "85% is an empirical out-of-sample gate, not a guarantee of future correctness"
        )
        return payload

    @app.get("/api/storage/prices")
    def price_storage() -> dict[str, Any]:
        return {
            "root": str(platform.price_store.root),
            "legacy_path": (
                None
                if platform.price_store.legacy_path is None
                else str(platform.price_store.legacy_path)
            ),
            "legacy_migration_pending": platform.price_store.needs_legacy_migration,
            "stats": asdict(platform.price_store.stats()),
        }

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        return model_catalog()

    @app.get("/api/models/first-touch/latest")
    @app.get("/api/predictions/latest")
    def latest_first_touch() -> list[dict[str, Any]]:
        frame = active_first_touch_ledger()
        if frame.empty:
            return []
        latest_anchor = int(frame["anchor_timestamp_ms"].max())
        subset = frame[frame["anchor_timestamp_ms"] == latest_anchor]
        return subset.where(subset.notna(), None).to_dict(orient="records")

    @app.get("/api/models/adaptive-shock/latest")
    @app.get("/api/envelope/latest")
    def latest_adaptive_shock() -> list[dict[str, Any]]:
        return active_envelope_predictions()

    @app.get("/api/ledger")
    def ledger(limit: int = 100) -> list[dict[str, Any]]:
        frame = active_first_touch_ledger()
        if frame.empty:
            return []
        frame = frame.sort_values("created_at_ms", ascending=False).head(
            max(1, min(limit, 1000))
        )
        return frame.where(frame.notna(), None).to_dict(orient="records")

    @app.get("/api/reports/training/first-touch")
    def first_touch_training_report() -> dict[str, Any]:
        return first_touch_training_report_payload()

    @app.get("/api/reports/training/adaptive-shock")
    def adaptive_shock_training_report() -> dict[str, Any]:
        path = platform.envelope.paths.report
        if not path.exists():
            return {"status": "WAIT", "reason": "no_adaptive_shock_training_report"}
        return json.loads(path.read_text(encoding="utf-8"))

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
    parser.add_argument("--checkpoint-rows", default=10_000, type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    platform = RealDataPlatformV2(
        RuntimePaths(),
        RuntimeConfig(
            bootstrap_start_ms=args.bootstrap_start_ms,
            minimum_final_rows_per_horizon=args.minimum_final_rows,
            retrain_after_new_final_rows=5_760,
            checkpoint_rows=args.checkpoint_rows,
        ),
    )
    uvicorn.run(create_app(platform), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
