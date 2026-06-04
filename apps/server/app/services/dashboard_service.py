"""Aggregation queries for the dashboard.

Pulls from the persistent `request_log` table. Returns plain dicts so the
templates don't need to know about ORM objects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import RequestLog
from app.db.session import get_sessionmaker


async def _burn_rate_widget() -> dict | None:
    """Burn-rate stats for the overview page: today's spend, the cap, the
    projected end-of-day spend, and whether we're past the soft cap.

    Returns None when MAX_DAILY_USD is 0 (no cap configured).
    """
    from datetime import datetime, timezone

    from app.core.settings import settings
    from app.services.budget_service import todays_spend_usd

    cap = float(settings.max_daily_usd or 0.0)
    if cap <= 0:
        return None

    spent = await todays_spend_usd()
    now = datetime.now(timezone.utc)
    seconds_elapsed = now.hour * 3600 + now.minute * 60 + now.second
    seconds_in_day = 86400
    # Avoid divide-by-zero in the first second of the day.
    pace_pct = seconds_elapsed / seconds_in_day if seconds_elapsed > 0 else 1 / seconds_in_day
    projected = spent / pace_pct if pace_pct > 0 else spent

    soft_cap_pct = float(settings.daily_soft_cap_pct or 0.0)
    soft_cap_usd = cap * soft_cap_pct if soft_cap_pct > 0 else 0.0
    in_soft_cap = soft_cap_pct > 0 and spent >= soft_cap_usd

    pct_used = (spent / cap) * 100 if cap > 0 else 0.0
    status = "ok"
    if spent >= cap:
        status = "exceeded"
    elif in_soft_cap:
        status = "soft_cap"
    elif projected >= cap:
        status = "projected_to_exceed"

    return {
        "spent_usd": spent,
        "cap_usd": cap,
        "projected_eod_usd": projected,
        "pct_used": pct_used,
        "soft_cap_usd": soft_cap_usd,
        "in_soft_cap": in_soft_cap,
        "status": status,
    }


def _provider_health_rows() -> list[dict]:
    """Snapshot every provider currently tracked. Returns a JSON-safe list."""
    # Imported here rather than at module top to avoid a cycle:
    # deps imports services modules; this service shouldn't reach back too early.
    from app.api.deps import get_health_tracker

    tracker = get_health_tracker()
    rows: list[dict] = []
    for snap in tracker.snapshot_all():
        rows.append(
            {
                "provider": snap.provider,
                "band": snap.band,
                "sample_size": snap.sample_size,
                "error_rate_pct": round(snap.error_rate * 100, 1),
                "latency_p50_ms": int(snap.latency_p50_ms),
                "latency_p95_ms": int(snap.latency_p95_ms),
            }
        )
    return rows


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

        # Richer task-type breakdown: count + total + avg cost. Surfaces
        # "you spent 60% of your money on reasoning prompts" type insights.
        by_task_full_q = (
            select(
                RequestLog.task_type,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
                func.coalesce(func.avg(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.task_type)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        by_task_full = [
            {
                "task_type": row[0] or "unknown",
                "count": int(row[1]),
                "total_usd": float(row[2] or 0.0),
                "avg_usd": float(row[3] or 0.0),
                "pct_of_total": 0.0,  # filled in below
            }
            for row in (await session.execute(by_task_full_q)).all()
        ]
        if total_usd > 0:
            for row in by_task_full:
                row["pct_of_total"] = 100.0 * row["total_usd"] / total_usd

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
        "task_breakdown": by_task_full,
        "recent": recent,
        "provider_health": _provider_health_rows(),
        "burn_rate": await _burn_rate_widget(),
    }


async def ab_cohort_comparison(window_days: int = 7) -> dict:
    """Per-cohort comparison: count, total cost, avg cost, avg latency proxy
    (we don't store latency per call yet, so this reports request-count and
    cost only — latency p50/p95 will land when we add per-request latency)."""
    cutoff = _utc_now() - timedelta(days=window_days)
    sm = get_sessionmaker()

    async with sm() as session:
        q = (
            select(
                RequestLog.cohort,
                RequestLog.selected_provider,
                RequestLog.selected_model,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
                func.coalesce(func.avg(RequestLog.estimated_usd), 0.0),
                func.sum(
                    func.coalesce(RequestLog.prompt_tokens, 0)
                    + func.coalesce(RequestLog.completion_tokens, 0)
                ),
                func.sum(
                    func.iif(RequestLog.status == "succeeded", 1, 0)
                ) if False else None,  # placeholder; computed below in Python for portability
            )
            .where(RequestLog.ts >= cutoff)
            .where(RequestLog.cohort.isnot(None))
            .group_by(RequestLog.cohort, RequestLog.selected_provider, RequestLog.selected_model)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        rows = (await session.execute(q)).all()

        # Compute success counts in a second pass — portable across dialects.
        success_q = (
            select(
                RequestLog.cohort,
                func.count(RequestLog.id),
            )
            .where(RequestLog.ts >= cutoff)
            .where(RequestLog.cohort.isnot(None))
            .where(RequestLog.status == "succeeded")
            .group_by(RequestLog.cohort)
        )
        success_map = {
            r[0]: int(r[1])
            for r in (await session.execute(success_q)).all()
        }
        total_q = (
            select(
                RequestLog.cohort,
                func.count(RequestLog.id),
            )
            .where(RequestLog.ts >= cutoff)
            .where(RequestLog.cohort.isnot(None))
            .group_by(RequestLog.cohort)
        )
        total_map = {
            r[0]: int(r[1])
            for r in (await session.execute(total_q)).all()
        }

    cohorts = []
    for row in rows:
        cohort, provider, model, count, total_usd, avg_usd, total_tokens, _ = row
        succ = success_map.get(cohort, 0)
        total = total_map.get(cohort, 0)
        success_rate = (succ / total * 100.0) if total else 0.0
        cohorts.append(
            {
                "cohort": cohort,
                "provider": provider or "—",
                "model": model or "—",
                "count": int(count),
                "total_usd": float(total_usd or 0.0),
                "avg_usd": float(avg_usd or 0.0),
                "total_tokens": int(total_tokens or 0),
                "success_rate_pct": success_rate,
            }
        )

    return {"window_days": window_days, "cohorts": cohorts}


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
            "channel": r.channel,
            "selected_provider": r.selected_provider,
            "selected_model": r.selected_model,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "estimated_usd": r.estimated_usd,
            "status": r.status,
            "attempts_count": len(r.attempts or []),
            "routing_reason": r.routing_reason,
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

        # Per-channel breakdown — surfaces "WhatsApp cost me $0.07, work Slack
        # cost me $2.34" type insights for OpenClaw and similar integrations.
        # Includes rows where channel is NULL (clients that don't tag).
        by_channel_q = (
            select(
                RequestLog.channel,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.estimated_usd), 0.0),
            )
            .where(RequestLog.ts >= cutoff)
            .group_by(RequestLog.channel)
            .order_by(func.coalesce(func.sum(RequestLog.estimated_usd), 0.0).desc())
        )
        by_channel = [
            {
                "channel": row[0] or "untagged",
                "count": int(row[1]),
                "usd": float(row[2] or 0.0),
            }
            for row in (await session.execute(by_channel_q)).all()
        ]

        # Daily cost — group by the date portion of ts.
        # `func.date()` is portable across SQLite and Postgres: SQLite returns
        # 'YYYY-MM-DD' strings, Postgres returns date objects. We normalise to
        # strings in Python so chart labels stay consistent.
        day_col = func.date(RequestLog.ts).label("day")
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
        daily_labels = [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in day_rows]
        daily_values = [float(r[1] or 0.0) for r in day_rows]

    return {
        "window_days": window_days,
        "total_requests": total_requests,
        "total_usd": total_usd,
        "avg_usd": avg_usd,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_channel": by_channel,
        "daily_chart": {"labels": daily_labels, "values": daily_values},
    }
