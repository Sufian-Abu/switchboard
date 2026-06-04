"""Aggregation queries for the dashboard.

Pulls from the persistent `request_log` table. Returns plain dicts so the
templates don't need to know about ORM objects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Date, cast, func, select

from app.db.models import RequestLog
from app.db.session import get_sessionmaker


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


async def overview(window_hours: int = 24) -> dict:
    """KPIs + breakdowns for the overview page."""
    cutoff = _utc_now() - timedelta(hours=window_hours)
    sm = get_sessionmaker()

    async with sm() as session:
        total_q = select(
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0),
        ).where(RequestLog.ts >= cutoff)
        total_rows = (await session.execute(total_q)).one()
        total_requests = int(total_rows[0])
        total_usd = float(total_rows[1] or 0.0)
        total_tokens = int(total_rows[2] or 0)

        success_q = (
            select(func.count(RequestLog.id))
            .where(RequestLog.ts >= cutoff)
            .where(RequestLog.status == "succeeded")
        )
        succeeded = int((await session.execute(success_q)).scalar_one())
        success_rate = (succeeded / total_requests * 100.0) if total_requests else 0.0

        by_provider_q = (
            select(
                RequestLog.selected_provider,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.selected_provider)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        by_provider = [
            {"provider": row[0] or "—", "count": int(row[1]), "usd": float(row[2] or 0.0)}
            for row in (await session.execute(by_provider_q)).all()
        ]

        cost_rows_q = (
            select(
                RequestLog.selected_provider,
                RequestLog.selected_model,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.selected_provider, RequestLog.selected_model)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        cost_rows = [
            {
                "provider": row[0] or "—",
                "model": row[1] or "—",
                "count": int(row[2]),
                "tokens": int(row[3] or 0),
                "usd": float(row[4] or 0.0),
            }
            for row in (await session.execute(cost_rows_q)).all()
        ]

        by_task_q = (
            select(RequestLog.task_type, func.count(RequestLog.id))
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.task_type)
            .order_by(func.count(RequestLog.id).desc())
        )
        by_task = [
            (row[0] or "unknown", int(row[1]))
            for row in (await session.execute(by_task_q)).all()
        ]

        recent_q = select(RequestLog).order_by(RequestLog.ts.desc()).limit(20)
        recent_orm = (await session.execute(recent_q)).scalars().all()
        recent = [
            {
                "ts_display": _fmt_ts(r.ts),
                "task_type": r.task_type,
                "selected_provider": r.selected_provider,
                "selected_model": r.selected_model,
                "total_tokens": (
                    (r.prompt_tokens or 0) + (r.completion_tokens or 0)
                    if r.prompt_tokens is not None or r.completion_tokens is not None
                    else None
                ),
                "estimated_usd": r.estimated_usd,
                "status": r.status,
            }
            for r in recent_orm
        ]

    return {
        "window_hours": window_hours,
        "total_requests": total_requests,
        "total_usd": total_usd,
        "total_tokens": total_tokens,
        "success_rate": success_rate,
        "provider_chart": {
            "labels": [r["provider"] for r in by_provider],
            "values": [r["usd"] for r in by_provider],
        },
        "task_chart": {
            "labels": [t[0] for t in by_task],
            "values": [t[1] for t in by_task],
        },
        "cost_rows": cost_rows,
        "recent": recent,
    }


async def recent_requests(limit: int = 100) -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as session:
        q = select(RequestLog).order_by(RequestLog.ts.desc()).limit(limit)
        rows_orm = (await session.execute(q)).scalars().all()
    return [
        {
            "ts_display": _fmt_ts(r.ts),
            "request_id": r.request_id,
            "task_type": r.task_type,
            "selected_provider": r.selected_provider,
            "selected_model": r.selected_model,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "estimated_usd": r.estimated_usd,
            "status": r.status,
            "attempts_count": len(r.attempts or []),
        }
        for r in rows_orm
    ]


async def cost_breakdown(window_days: int = 7) -> dict:
    cutoff = _utc_now() - timedelta(days=window_days)
    sm = get_sessionmaker()

    async with sm() as session:
        totals_q = select(
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
        ).where(RequestLog.ts >= cutoff)
        c_rows = (await session.execute(totals_q)).one()
        total_requests = int(c_rows[0])
        total_usd = float(c_rows[1] or 0.0)
        avg_usd = total_usd / total_requests if total_requests else 0.0

        by_provider_q = (
            select(
                RequestLog.selected_provider,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.selected_provider)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        by_provider = [
            {"provider": row[0] or "—", "count": int(row[1]), "usd": float(row[2] or 0.0)}
            for row in (await session.execute(by_provider_q)).all()
        ]

        by_model_q = (
            select(
                RequestLog.selected_model,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.selected_model)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        by_model = [
            {"model": row[0] or "—", "count": int(row[1]), "usd": float(row[2] or 0.0)}
            for row in (await session.execute(by_model_q)).all()
        ]

        # Daily cost — group by the date portion of ts. `cast(ts, Date)` is
        # portable across SQLite and Postgres (and works with any other
        # SQLAlchemy dialect we might add later).
        day_col = cast(RequestLog.ts, Date).label("day")
        day_q = (
            select(
                day_col,
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(day_col)
            .order_by(day_col)
        )
        day_rows = (await session.execute(day_q)).all()
        # The cast returns date objects on Postgres and strings on SQLite —
        # normalise to YYYY-MM-DD for chart labels.
        daily_labels = [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in day_rows]
        daily_values = [float(r[1] or 0.0) for r in day_rows]

    return {
        "window_days": window_days,
        "total_requests": total_requests,
        "total_usd": total_usd,
        "avg_usd": avg_usd,
        "by_provider": by_provider,
        "by_model": by_model,
        "daily_chart": {"labels": daily_labels, "values": daily_values},
    }
