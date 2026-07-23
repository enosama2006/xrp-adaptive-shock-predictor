"""Read-only governance, discovery evidence, and API routes."""

from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class GovernanceEvidenceReader:
    integrity_path: Path
    expansion_path: Path
    discovery_path: Path

    @classmethod
    def from_platform(cls, platform: RealDataPlatformV2) -> GovernanceEvidenceReader:
        return cls(
            integrity_path=platform.paths.reports.parent / "data_integrity.json",
            expansion_path=platform.paths.state.parent / "history_expansion_state.json",
            discovery_path=platform.paths.reports.parent / "first_passage_discovery.json",
        )

    def integrity_payload(self) -> dict[str, Any]:
        if not self.integrity_path.exists():
            return {
                "status": "WAIT",
                "reason": "no_data_integrity_report",
                "report_path": str(self.integrity_path),
            }
        payload = _json_object(self.integrity_path)
        payload["report_path"] = str(self.integrity_path)
        payload["report_updated_at_ms"] = int(
            self.integrity_path.stat().st_mtime * 1000
        )
        return payload

    def expansion_payload(self) -> dict[str, Any]:
        if not self.expansion_path.exists():
            return {
                "status": "IDLE",
                "reason": "no_history_expansion_requested",
                "completed": False,
                "progress_fraction": 0.0,
                "state_path": str(self.expansion_path),
            }
        payload = _json_object(self.expansion_path)
        completed = bool(payload.get("completed", False))
        payload["status"] = "READY" if completed else "WAIT"
        payload["reason"] = (
            "history_expansion_completed"
            if completed
            else "history_expansion_checkpointed_incomplete"
        )
        payload["progress_fraction"] = _history_progress(payload)
        payload["state_path"] = str(self.expansion_path)
        payload["state_updated_at_ms"] = int(
            self.expansion_path.stat().st_mtime * 1000
        )
        return payload

    def discovery_payload(self) -> dict[str, Any]:
        if not self.discovery_path.exists():
            return {
                "status": "WAIT",
                "reason": "no_first_passage_discovery_report",
                "report_path": str(self.discovery_path),
            }
        payload = _json_object(self.discovery_path)
        payload["report_path"] = str(self.discovery_path)
        payload["report_updated_at_ms"] = int(
            self.discovery_path.stat().st_mtime * 1000
        )
        return payload

    def summary_payload(self) -> dict[str, Any]:
        integrity = self.integrity_payload()
        expansion = self.expansion_payload()
        discovery = self.discovery_payload()
        return {
            "data_integrity": integrity,
            "history_expansion": expansion,
            "first_passage_discovery": discovery,
            "training_allowed_by_platform_policy": integrity.get("status") != "FAIL",
            "trading_promoted": False,
        }


def build_governance_router(platform: RealDataPlatformV2) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["governance"])
    reader = GovernanceEvidenceReader.from_platform(platform)
    latest_market_cache: dict[str, Any] = {
        "timestamp_ms": None,
        "payload": None,
    }

    @router.get("/reports/data-integrity")
    def data_integrity_report() -> dict[str, Any]:
        return reader.integrity_payload()

    @router.get("/history-expansion")
    def history_expansion_status() -> dict[str, Any]:
        return reader.expansion_payload()

    @router.get("/research/first-passage")
    def first_passage_discovery() -> dict[str, Any]:
        return reader.discovery_payload()

    @router.get("/market/latest")
    def latest_market() -> dict[str, Any]:
        stats = platform.price_store.stats()
        timestamp_ms = stats.max_timestamp_ms
        if timestamp_ms is None:
            return {
                "status": "WAIT",
                "reason": "no_observed_price_rows",
            }
        if latest_market_cache["timestamp_ms"] == timestamp_ms:
            cached = latest_market_cache["payload"]
            if isinstance(cached, dict):
                return cast(dict[str, Any], cached)
        frame = platform.price_store.load(
            start_ms=timestamp_ms,
            end_ms=timestamp_ms,
        )
        if frame.empty:
            return {
                "status": "WAIT",
                "reason": "latest_price_row_not_found",
                "timestamp_ms": timestamp_ms,
            }
        row = frame.iloc[-1]
        payload = {
            "status": "READY",
            "symbol": platform.config.symbol,
            "timestamp_ms": int(row["timestamp_ms"]),
            "price": float(row["price"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "volume": float(row["volume"]),
            "quote_volume": (
                None if row.get("quote_volume") is None else float(row["quote_volume"])
            ),
            "source": "latest_observed_completed_one_minute_candle",
        }
        latest_market_cache["timestamp_ms"] = timestamp_ms
        latest_market_cache["payload"] = payload
        return payload

    @router.get("/governance")
    def governance_summary() -> dict[str, Any]:
        return reader.summary_payload()

    return router


__all__ = ["GovernanceEvidenceReader", "build_governance_router"]
