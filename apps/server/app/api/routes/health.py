"""GET /health — liveness probe. GET /v1/cache/stats — semantic cache stats. GET /metrics — Prometheus."""
from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import get_semantic_cache
from app.core.settings import settings
from app.metrics import render_metrics
from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        environment=settings.app_env,
    )


@router.get("/v1/cache/stats")
async def cache_stats() -> dict:
    """Return semantic cache stats, or `{enabled: false}` when disabled."""
    cache = get_semantic_cache()
    if cache is None:
        return {"enabled": False}
    return {"enabled": True, **cache.snapshot()}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus metrics endpoint. Returns plain text in the prometheus format."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
