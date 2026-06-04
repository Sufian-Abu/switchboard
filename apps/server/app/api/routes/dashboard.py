"""HTML dashboard routes (overview, recent requests, cost breakdown)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import dashboard_service

# Templates live at apps/server/app/templates.
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_overview(request: Request) -> HTMLResponse:
    data = await dashboard_service.overview(window_hours=24)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/overview.html",
        context=data,
    )


@router.get("/dashboard/requests", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_requests(request: Request) -> HTMLResponse:
    rows = await dashboard_service.recent_requests(limit=100)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/requests.html",
        context={"rows": rows},
    )


@router.get("/dashboard/cost", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_cost(request: Request) -> HTMLResponse:
    data = await dashboard_service.cost_breakdown(window_days=7)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/cost.html",
        context=data,
    )


@router.get("/dashboard/playground", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_playground(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard/playground.html",
        context={},
    )
