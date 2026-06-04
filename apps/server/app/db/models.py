"""SQLAlchemy table definitions for the persistent request log."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestLog(Base):
    """One row per chat-completion request (succeeded or failed)."""

    __tablename__ = "request_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    endpoint: Mapped[str] = mapped_column(String(64), default="/v1/chat/completions")
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    selected_provider: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    selected_model: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Extracted from request.metadata.channel for per-channel analytics in
    # OpenClaw-style integrations. Nullable for clients that don't tag.
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(16), index=True)  # 'succeeded' | 'failed'
    attempts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Human-readable "why was this provider chosen?" — surfaced verbatim on the
    # dashboard so users can trust the routing decision.
    routing_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # A/B cohort label when the matched rule used `split:`. NULL otherwise.
    cohort: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
