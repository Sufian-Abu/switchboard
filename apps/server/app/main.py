"""FastAPI application entrypoint.

Wires up routes, middleware, exception handlers, and startup/shutdown hooks.
Running `uvicorn app.main:app` is the canonical way to launch the server.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.deps import (
    get_classifier,
    get_config,
    get_cost_engine,
    get_decision_engine,
)
from app.api.router import api_router
from app.core.settings import settings
from app.db.session import dispose_engine, init_engine
from app.services.budget_service import BudgetExceededError
from router.errors import ConfigError, ProviderError, RouterError
from router.logger import get_logger

log = get_logger("llm-router.app")


# --- Lifespan ---------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Warm caches at startup so first-request latency is normal.

    Also acts as a fast-fail boot check: if the YAML config is missing or
    malformed, the server refuses to start instead of returning 500s later.
    """
    log.info("starting %s (env=%s)", settings.app_name, settings.app_env)
    try:
        get_config()
        get_classifier()
        get_decision_engine()
        get_cost_engine()
        await init_engine()
    except RouterError as exc:
        log.error("startup failed: %s", exc)
        raise
    log.info("startup complete")
    yield
    log.info("shutdown")
    await dispose_engine()


# --- Middleware -------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign each request a short ID and echo it back via `x-request-id`.

    Honors an inbound `x-request-id` header if the client provides one, so a
    request can be traced across services.
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on protected paths.

    - `/v1/*` paths always require `Authorization: Bearer <api_token>` when
      `settings.api_token` is set.
    - `/dashboard/*` and `/v1/cache/stats` also require auth when
      `settings.dashboard_auth` is True. For browser convenience these paths
      accept either a Bearer header (programmatic) OR HTTP Basic auth
      (username ignored, password = api_token). The browser prompts the user
      with a native auth dialog.

    No-op when `settings.api_token` is empty (default).
    """

    _DASHBOARD_PREFIXES = ("/dashboard", "/v1/cache/stats")

    def _is_v1_protected(self, path: str) -> bool:
        return path.startswith("/v1/")

    def _is_dashboard_protected(self, path: str) -> bool:
        return any(path.startswith(p) for p in self._DASHBOARD_PREFIXES)

    def _credentials_valid(self, header: str, expected: str) -> bool:
        """Accept `Bearer <token>` or `Basic <b64(user:token)>`."""
        scheme, _, value = header.partition(" ")
        scheme = scheme.lower()
        if scheme == "bearer":
            return value == expected
        if scheme == "basic":
            import base64
            try:
                decoded = base64.b64decode(value).decode("utf-8", errors="replace")
            except Exception:
                return False
            _, _, password = decoded.partition(":")
            return password == expected
        return False

    async def dispatch(self, request: Request, call_next):
        token = settings.api_token
        if not token:
            return await call_next(request)

        path = request.url.path
        is_v1 = self._is_v1_protected(path)
        is_dash = settings.dashboard_auth and self._is_dashboard_protected(path)
        if not is_v1 and not is_dash:
            return await call_next(request)

        if self._credentials_valid(request.headers.get("authorization", ""), token):
            return await call_next(request)

        rid = getattr(request.state, "request_id", "-")
        # Dashboard requests come from browsers — return a WWW-Authenticate header
        # so the browser pops a credential prompt.
        if is_dash:
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "unauthorized", "request_id": rid}},
                headers={"WWW-Authenticate": 'Basic realm="Switchboard dashboard"'},
            )
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "type": "unauthorized",
                    "message": "Missing or invalid bearer token.",
                    "request_id": rid,
                }
            },
        )


# --- App --------------------------------------------------------------------


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="OpenAI-compatible chat endpoint with task-aware provider routing.",
    lifespan=lifespan,
)

# Add inner middleware first so the outermost runs first per Starlette semantics.
# Order at request time: RequestID (assign rid) → BearerAuth (may reject) → handler.
app.add_middleware(BearerAuthMiddleware)
app.add_middleware(RequestIDMiddleware)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect bare-host hits to the dashboard."""
    return RedirectResponse(url="/dashboard")


# --- Exception handlers -----------------------------------------------------


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    rid = getattr(request.state, "request_id", "-")
    log.warning("[%s] provider_error provider=%s msg=%s", rid, exc.provider, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": "provider_error",
                "provider": exc.provider,
                "message": exc.message,
                "request_id": rid,
            }
        },
    )


@app.exception_handler(BudgetExceededError)
async def budget_exceeded_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
    rid = getattr(request.state, "request_id", "-")
    log.warning(
        "[%s] budget_exceeded spent=%.4f cap=%.4f path=%s",
        rid,
        exc.spent,
        exc.cap,
        request.url.path,
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "budget_exceeded",
                "message": str(exc),
                "spent_usd": exc.spent,
                "cap_usd": exc.cap,
                "request_id": rid,
            }
        },
    )


@app.exception_handler(ConfigError)
async def config_error_handler(request: Request, exc: ConfigError) -> JSONResponse:
    rid = getattr(request.state, "request_id", "-")
    log.error("[%s] config_error msg=%s", rid, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "config_error",
                "message": str(exc),
                "request_id": rid,
            }
        },
    )
