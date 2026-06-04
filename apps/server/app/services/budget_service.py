"""Daily spend circuit-breaker.

Reads the persistent request log and refuses new chat requests once today's
recorded `estimated_usd` total crosses `settings.max_daily_usd`. Disabled
when the cap is 0 (the default).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.settings import settings
from app.db.models import RequestLog
from app.db.session import get_sessionmaker


def _start_of_day_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def todays_spend_usd() -> float:
    """Return the sum of estimated_usd for log rows from the current UTC day."""
    try:
        sm = get_sessionmaker()
    except RuntimeError:
        return 0.0  # DB not initialised (e.g. some test paths) — never trip the breaker.
    cutoff = _start_of_day_utc()
    async with sm() as session:
        total = await session.execute(
            select(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0))
            .where(RequestLog.ts >= cutoff)
            .where(RequestLog.status == "succeeded")
        )
        return float(total.scalar_one() or 0.0)


async def assert_under_daily_cap() -> None:
    """Raise BudgetExceededError when settings.max_daily_usd is hit. No-op when cap is 0."""
    cap = float(settings.max_daily_usd or 0.0)
    if cap <= 0:
        return
    spent = await todays_spend_usd()
    if spent >= cap:
        raise BudgetExceededError(spent=spent, cap=cap)


class BudgetExceededError(Exception):
    """Raised by `assert_under_daily_cap()` when today's spend ≥ MAX_DAILY_USD."""

    def __init__(self, spent: float, cap: float) -> None:
        self.spent = spent
        self.cap = cap
        super().__init__(
            f"Daily spend cap reached: ${spent:.4f} of ${cap:.4f}. "
            f"Cap resets at 00:00 UTC."
        )
