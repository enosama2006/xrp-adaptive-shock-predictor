"""Read-only governance endpoints for observed-data evidence and history expansion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter

from .history_expansion import MINUTE_MS
from .platform_runtime_v2 import RealDataPlatformV2


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"governance JSON must contain an object: {path}")
    return cast(dict[str, Any], payload)


def _history_progress(payload: dict[str, Any]) -> float:
    if bool(payload.get("completed", False)):
        return 1.0
    start = int(payload.get("target_start_ms", 0))
    end = int(payload.get("target_end_ms", start))
    next_open = int(payload.get("next_open_time_ms", max(0, start - MINUTE_MS)))
    span = max(MINUTE_MS, end - start + MINUTE_MS)
    completed_span = max(0, next_open + MINUTE_MS - start)
    return min(1.0, completed_span / span)


def build_governance_router(platform: RealDataPlatformV2) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["governance"])
    integrity_path = platform.paths.reports.parent / "data_integrity.json"
    expansion_path = platform.paths.state.parent / "history_expansion_state.json"

    def integrity_payload() -> dict[str, Any]:
        if not integrity_path.exists():
            return {
                "status": "WAIT",
                "reason": "no_data_integrity_report",
                "report_path": str(integrity_path),
            }
        payload = _json_object(integrity_path)
        payload["report_path"] = str(integrity_path)
        payload["report_updated_at_ms"] = int(integrity_path.stat().st_mtime * 1000)
        return payload

    def expansion_payload() -> dict[str, Any]:
        if not expansion_path.exists():
            return {
                "status": "IDLE",
                "reason": "no_history_expansion_requested",
                "completed": False,
                "progress_fraction": 0.0,
                "state_path": str(expansion_path),
            }
        payload = _json_object(expansion_path)
        completed = bool(payload.get("completed", False))
        payload["status"] = "READY" if completed else "WAIT"
        payload["reason"] = (
            "history_expansion_completed"
            if completed
            else "history_expansion_checkpointed_incomplete"
        )
        payload["progress_fraction"] = _history_progress(payload)
        payload["state_path"] = str(expansion_path)
        payload["state_updated_at_ms"] = int(expansion_path.stat().st_mtime * 1000)
        return payload

    @router.get("/reports/data-integrity")
    def data_integrity_report() -> dict[str, Any]:
        return integrity_payload()

    @router.get("/history-expansion")
    def history_expansion_status() -> dict[str, Any]:
        return expansion_payload()

    @router.get("/governance")
    def governance_summary() -> dict[str, Any]:
        integrity = integrity_payload()
        expansion = expansion_payload()
        return {
            "data_integrity": integrity,
            "history_expansion": expansion,
            "training_allowed_by_platform_policy": integrity.get("status") != "FAIL",
            "trading_promoted": False,
        }

    return router


__all__ = ["build_governance_router"]
