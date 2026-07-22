"""FastAPI service for the real-data XASP platform."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .platform_runtime import RealDataPlatform, RuntimeConfig, RuntimePaths


def create_app(platform: RealDataPlatform, web_root: Path = Path(".")) -> FastAPI:
    app = FastAPI(title="XASP Real Data Platform", version="0.6.0")
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
                except Exception as exc:  # fail closed and expose the real error
                    platform.status.state = "WAIT"
                    platform.status.reason = f"runtime_error:{type(exc).__name__}:{exc}"
                await asyncio.sleep(60)

        app.state.worker = asyncio.create_task(worker())

    @app.on_event("shutdown")
    async def stop_worker() -> None:
        task = getattr(app.state, "worker", None)
        if task is not None:
            task.cancel()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return asdict(platform.status)

    @app.get("/api/predictions/latest")
    def latest_predictions() -> list[dict[str, Any]]:
        frame = platform.ledger.load()
        if frame.empty:
            return []
        latest_anchor = int(frame["anchor_timestamp_ms"].max())
        return frame[frame["anchor_timestamp_ms"] == latest_anchor].to_dict(orient="records")

    @app.get("/api/ledger")
    def ledger(limit: int = 100) -> list[dict[str, Any]]:
        frame = platform.ledger.load().sort_values("created_at_ms", ascending=False).head(max(1, min(limit, 1000)))
        return frame.where(frame.notna(), None).to_dict(orient="records")

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

    app.mount("/static", StaticFiles(directory=web_root), name="static")
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run XASP with real Binance data only")
    parser.add_argument("--bootstrap-start-ms", required=True, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--minimum-final-rows", default=2_000, type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    platform = RealDataPlatform(
        RuntimePaths(),
        RuntimeConfig(
            bootstrap_start_ms=args.bootstrap_start_ms,
            minimum_final_rows_per_horizon=args.minimum_final_rows,
        ),
    )
    uvicorn.run(create_app(platform), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
