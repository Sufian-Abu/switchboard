"""Shared HTTP-facing schemas."""
from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Body returned by `GET /health`."""

    status: str
    app_name: str
    environment: str
