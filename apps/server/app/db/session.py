"""Async SQLAlchemy engine + session factory.

Initialized lazily so importing this module doesn't open a DB connection.
The engine is built once per process and held in module-level state — call
`init_engine()` from the app lifespan to set it up, `dispose_engine()` to tear
it down.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings
from app.db.models import Base

# `_state` holds the singletons. Indirect via mutable dict so reset_engine()
# tests can replace them cleanly.
_state: dict = {"engine": None, "sessionmaker": None}


def _resolve_db_url() -> str:
    """If sqlite path is relative, anchor it to the project root and ensure parent dir exists."""
    url = settings.database_url
    sqlite_prefix = "sqlite+aiosqlite:///"
    if url.startswith(sqlite_prefix):
        path_part = url[len(sqlite_prefix):]
        path = Path(path_part)
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parents[4]
            path = (project_root / path_part).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{sqlite_prefix}{path}"
    return url


async def init_engine() -> None:
    """Build engine + sessionmaker and create tables. Safe to call multiple times."""
    if _state["engine"] is not None:
        return
    engine: AsyncEngine = create_async_engine(_resolve_db_url(), future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _state["engine"] = engine
    _state["sessionmaker"] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


async def dispose_engine() -> None:
    """Tear down the engine. Used at shutdown and in tests."""
    engine = _state.get("engine")
    if engine is not None:
        await engine.dispose()
    _state["engine"] = None
    _state["sessionmaker"] = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the configured sessionmaker. Raises RuntimeError if init wasn't called."""
    sm = _state.get("sessionmaker")
    if sm is None:
        raise RuntimeError("DB engine not initialized; call init_engine() in lifespan.")
    return sm
