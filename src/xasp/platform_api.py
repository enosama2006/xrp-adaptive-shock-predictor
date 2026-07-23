"""FastAPI service for the real-data XASP platform."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any, cast

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .first_touch_v4 import FIRST_TOUCH_GATE_VERSION
from .governance_routes import build_governance_router
from .horizons import (
    RESEARCH_HORIZON_KEYS,
    RESEARCH_HORIZON_SET_VERSION,
    RESEARCH_HORIZONS_MINUTES,
)
from .platform_runtime_v2 import RealDataPlatformV2, RuntimeConfig, RuntimePaths
from .production_report_v2 import ProductionReportPaths

HORIZON_KEYS = RESEARCH_HORIZON_KEYS


def _record_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("record payload must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("record payload entries must be objects")
        records.append(cast(dict[str, Any], item))
    return records


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON report must contain an object: {path}")
    return cast(dict[str, Any], value)


def _bundle_horizons(bundle: dict[str, Any] | None) -> list[int]:
    if bundle is None:
        return []
    return sorted(int(value) for value in bundle.get("models", {}))


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
        version="1.4.2",
        lifespan=lifespan,
    )
    app.include_router(build_governance_router(platform))

    def first_touch_training_report_payload() -> dict[str, Any]:
        path = platform.paths.reports
        if not path.exists():
            return {
                "_meta": {
                    "status": "WAIT",
                    "reason": "no_first_touch_training_report",
                    "is_current": False,
                    "current_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
                    "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
                    "configured_horizons": list(RESEARCH_HORIZONS_MINUTES),
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
        statuses = {key: str(report.get("status", "WAIT")) for key, report in horizons.items()}
        reasons = {key: str(report.get("reason", "unknown")) for key, report in horizons.items()}
        walk_forward = {
            key: report.get("metrics", {}).get("walk_forward_support_audit")
            for key, report in horizons.items()
            if report.get("metrics", {}).get("walk_forward_support_audit") is not None
        }
        meta = {
            "status": "CURRENT" if current else "STALE" if horizons else "WAIT",
            "reason": (
                "report_matches_current_independent_horizon_gate"
                if current
                else "report_was_generated_by_an_older_gate_or_training_is_still_running"
            ),
            "is_current": current,
            "current_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "configured_horizons": list(RESEARCH_HORIZONS_MINUTES),
            "report_gate_methodology_versions": versions,
            "horizon_statuses": statuses,
            "horizon_reasons": reasons,
            "walk_forward_support_by_horizon": walk_forward,
            "report_updated_at_ms": int(path.stat().st_mtime * 1000),
            "model_available": platform._bundle is not None,
            "available_horizons": _bundle_horizons(platform._bundle),
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

    def active_envelope_predictions() -> list[dict[str, Any]]:
        if platform.envelope.bundle is None or not platform.envelope.paths.predictions.exists():
            return []
        frame = platform._active_envelope_predictions()
        if frame.empty:
            return []
        latest_anchor = int(frame["anchor_timestamp_ms"].max())
        subset = frame[frame["anchor_timestamp_ms"] == latest_anchor]
        return _record_list(subset.where(subset.notna(), None).to_dict(orient="records"))

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
        touch_available = _bundle_horizons(touch_bundle)
        shock_available = _bundle_horizons(shock_bundle)
        configured = list(RESEARCH_HORIZONS_MINUTES)

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
                "configured_horizons_minutes": configured,
                "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
                "retraining_policy": (
                    f"after {platform.config.retrain_after_new_final_rows:,} "
                    "newly finalized horizon rows (~1 day across eight horizons)"
                ),
                "source": "Binance public observed market data",
            },
            "adaptive_shock": {
                "display_name": "Adaptive Shock Magnitude Model",
                "technical_name": "future-excursion quantile regression",
                "purpose": (
                    "Estimate upside and downside excursion ranges independently for "
                    "15/30/45/60/120/180/240/480 minutes."
                ),
                "available": bool(shock_available),
                "available_horizons": shock_available,
                "waiting_horizons": [h for h in configured if h not in shock_available],
                "availability_reason": (
                    "one_or_more_historical_interval_gates_passed"
                    if shock_available
                    else "no_valid_adaptive_shock_horizon"
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
                "gate": (
                    "Independent 85% empirical marginal interval coverage gate per horizon "
                    "on untouched temporal test data"
                ),
                "endpoint": "/api/models/adaptive-shock/latest",
                "training_report_endpoint": "/api/reports/training/adaptive-shock",
            },
            "first_touch_10": {
                "display_name": "±10% First-Touch Model",
                "technical_name": "calibrated multiclass first-touch classifier",
                "purpose": (
                    "Estimate whether +10%, -10%, or neither is reached first "
                    "within each independent horizon through eight hours."
                ),
                "available": bool(touch_available),
                "available_horizons": touch_available,
                "waiting_horizons": [h for h in configured if h not in touch_available],
                "availability_reason": (
                    "one_or_more_independent_directional_horizon_gates_passed"
                    if touch_available
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
                    "Each horizon needs multiple purged untouched periods with sufficient "
                    "independent UP_10 and DOWN_10 event clusters, then at least 85% empirical "
                    "precision for high-confidence directional predictions. NO_EVENT cannot "
                    "pass the directional gate."
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
            "shock_targets": platform.envelope.target_store.exists,
            "shock_target_partitions": platform.envelope.target_store.stats().partition_count,
            "shock_model_file": platform.envelope.paths.model.exists(),
            "shock_report": platform.envelope.paths.report.exists(),
            "production_report": report_paths.latest_json.exists(),
            "production_report_history": report_paths.history_jsonl.exists(),
        }
        touch_horizons = _bundle_horizons(platform._bundle)
        shock_horizons = _bundle_horizons(platform.envelope.bundle)
        configured = set(RESEARCH_HORIZONS_MINUTES)
        return {
            "service": "UP",
            "runtime_state": platform.status.state,
            "runtime_reason": platform.status.reason,
            "lifecycle_stage": platform.status.lifecycle_stage,
            "lifecycle_progress": platform.status.lifecycle_progress,
            "lifecycle_message": platform.status.lifecycle_message,
            "data_available": storage["prices"] and storage["features"],
            "price_store": asdict(price_stats),
            "configured_horizons_minutes": list(RESEARCH_HORIZONS_MINUTES),
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "first_touch_available_horizons": touch_horizons,
            "adaptive_shock_available_horizons": shock_horizons,
            "first_touch_model_available": bool(touch_horizons),
            "adaptive_shock_model_available": bool(shock_horizons),
            "first_touch_gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "storage": storage,
            "ready_for_first_touch_research": bool(touch_horizons),
            "ready_for_adaptive_shock_research": bool(shock_horizons),
            "ready_for_any_research_prediction": bool(touch_horizons or shock_horizons),
            "ready_for_all_research_predictions": (
                set(touch_horizons) == configured and set(shock_horizons) == configured
            ),
            "ready_for_trading": False,
        }

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        payload = asdict(platform.status)
        payload["configured_horizons_minutes"] = list(RESEARCH_HORIZONS_MINUTES)
        payload["horizon_set_version"] = RESEARCH_HORIZON_SET_VERSION
        payload["adaptive_shock_available_horizons"] = _bundle_horizons(platform.envelope.bundle)
        payload["first_touch_available_horizons"] = _bundle_horizons(platform._bundle)
        payload["adaptive_shock_model_available"] = bool(
            payload["adaptive_shock_available_horizons"]
        )
        payload["first_touch_model_available"] = bool(payload["first_touch_available_horizons"])
        payload["first_touch_gate_methodology_version"] = FIRST_TOUCH_GATE_VERSION
        payload["required_empirical_confidence"] = 0.85
        payload["confidence_note"] = (
            "85% is an empirical out-of-sample gate, not a guarantee of future correctness"
        )
        return payload

    @app.get("/api/horizons")
    def horizons() -> dict[str, Any]:
        return {
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "horizons_minutes": list(RESEARCH_HORIZONS_MINUTES),
            "semantics": (
                "Each horizon inspects every completed one-minute candle after the anchor "
                "through the inclusive horizon end."
            ),
            "independent_gates": True,
            "trading_promoted": False,
        }

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
        return _record_list(subset.where(subset.notna(), None).to_dict(orient="records"))

    @app.get("/api/models/adaptive-shock/latest")
    @app.get("/api/envelope/latest")
    def latest_adaptive_shock() -> list[dict[str, Any]]:
        return active_envelope_predictions()

    @app.get("/api/ledger")
    def ledger(limit: int = 100) -> list[dict[str, Any]]:
        frame = active_first_touch_ledger()
        if frame.empty:
            return []
        frame = frame.sort_values("created_at_ms", ascending=False).head(max(1, min(limit, 1000)))
        return _record_list(frame.where(frame.notna(), None).to_dict(orient="records"))

    @app.get("/api/reports/training/first-touch")
    def first_touch_training_report() -> dict[str, Any]:
        return first_touch_training_report_payload()

    @app.get("/api/reports/training/adaptive-shock")
    def adaptive_shock_training_report() -> dict[str, Any]:
        path = platform.envelope.paths.report
        if not path.exists():
            return {"status": "WAIT", "reason": "no_adaptive_shock_training_report"}
        return _json_object(path)

    @app.get("/api/reports/production")
    def production_report() -> dict[str, Any]:
        path = ProductionReportPaths().latest_json
        if not path.exists():
            return platform.generate_production_report()
        return _json_object(path)

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

    @app.get("/governance.js")
    def governance_javascript() -> FileResponse:
        return FileResponse(
            web_root / "governance.js",
            media_type="application/javascript",
        )

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
            retrain_after_new_final_rows=11_520,
            checkpoint_rows=args.checkpoint_rows,
        ),
    )
    uvicorn.run(create_app(platform), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
