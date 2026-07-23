"""FastAPI routes for the non-persistent model research laboratory."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .model_lab import ModelLabService
from .platform_runtime_v2 import RealDataPlatformV2


class LabPredictionRequest(BaseModel):
    model_key: Literal["adaptive_shock", "first_touch"]
    horizon_minutes: int
    input_source: Literal["manual", "current_market"] = "manual"
    anchor_price: float | None = Field(default=None, gt=0)
    feature_values: dict[str, float | None] = Field(default_factory=dict)


def build_lab_router(
    platform: RealDataPlatformV2,
    web_root: Path = Path("."),
) -> APIRouter:
    router = APIRouter(tags=["model-lab"])
    service = ModelLabService(platform)

    @router.get("/lab/overview")
    def lab_overview() -> dict[str, object]:
        return service.overview_payload()

    @router.get("/lab/current-inputs")
    def lab_current_inputs() -> dict[str, object]:
        return service.current_inputs_payload()

    @router.post("/lab/predict")
    def lab_predict(request: LabPredictionRequest) -> dict[str, object]:
        return service.predict_payload(
            model_key=request.model_key,
            horizon_minutes=request.horizon_minutes,
            input_source=request.input_source,
            anchor_price=request.anchor_price,
            feature_values=request.feature_values,
        )

    @router.get("/model-lab", include_in_schema=False)
    def lab_page() -> FileResponse:
        return FileResponse(web_root / "lab.html")

    @router.get("/lab.js", include_in_schema=False)
    def lab_javascript() -> FileResponse:
        return FileResponse(web_root / "lab.js", media_type="application/javascript")

    @router.get("/lab.css", include_in_schema=False)
    def lab_stylesheet() -> FileResponse:
        return FileResponse(web_root / "lab.css", media_type="text/css")

    return router


__all__ = ["LabPredictionRequest", "build_lab_router"]
