"""Composes sub-routers into a single `api_router` mounted by `main.py`."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.chat import router as chat_router
from app.api.routes.compare import router as compare_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.estimate import router as estimate_router
from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(chat_router, tags=["chat"])
api_router.include_router(estimate_router, tags=["chat"])
api_router.include_router(compare_router, tags=["chat"])
api_router.include_router(dashboard_router, tags=["dashboard"])
