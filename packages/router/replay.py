"""Routing Replay — backtest a candidate routing config against historical traffic.

You log every request through Switchboard with its task_type, channel, token
counts, and the provider/model that actually served it. Replay reads that log,
re-runs the decision engine against a **candidate** config (not the live one),
and reports what would have changed: cost diff per task type and per channel,
provider distribution shift, and total projected change.

No upstream calls. No tokens spent. You decide whether to ship the new config
based on what the historical traffic shows.

Usage::

    python -m router.replay --config configs/new.yaml
    python -m router.replay --config configs/new.yaml --since 2026-06-01 --limit 1000
    python -m router.replay --config configs/new.yaml --pricing configs/pricing.yaml

Exits 0 always — this is an analysis tool, not a guard. CI integrations can
parse the structured output (--json) and act on the cost delta themselves.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.schemas import ClassifiedTask


@dataclass
class _Row:
    """One historical request log row, just the fields we need to replay."""

    request_id: str
    ts: str
    task_type: str | None
    channel: str | None
    actual_provider: str | None
    actual_model: str | None
    prompt_tokens: int
    completion_tokens: int
    actual_usd: float


@dataclass
class _AggBucket:
    """Per-group totals (task_type, channel, etc.)."""

    actual_calls: int = 0
    new_calls: int = 0
    actual_usd: float = 0.0
    new_usd: float = 0.0


@dataclass
class ReplayReport:
    """Structured report. Used directly by tests and serialised by the CLI."""

    total_rows: int
    skipped: int
    actual_total_usd: float
    new_total_usd: float
    by_task: dict[str, _AggBucket] = field(default_factory=dict)
    by_channel: dict[str, _AggBucket] = field(default_factory=dict)
    actual_provider_counts: dict[str, int] = field(default_factory=dict)
    new_provider_counts: dict[str, int] = field(default_factory=dict)

    @property
    def cost_delta_usd(self) -> float:
        return self.new_total_usd - self.actual_total_usd

    @property
    def cost_delta_pct(self) -> float:
        if self.actual_total_usd <= 0:
            return 0.0
        return 100.0 * self.cost_delta_usd / self.actual_total_usd

    def to_dict(self) -> dict:
        def _bucket(b: _AggBucket) -> dict:
            return {
                "actual_calls": b.actual_calls,
                "new_calls": b.new_calls,
                "actual_usd": round(b.actual_usd, 8),
                "new_usd": round(b.new_usd, 8),
            }

        return {
            "total_rows": self.total_rows,
            "skipped": self.skipped,
            "actual_total_usd": round(self.actual_total_usd, 8),
            "new_total_usd": round(self.new_total_usd, 8),
            "cost_delta_usd": round(self.cost_delta_usd, 8),
            "cost_delta_pct": round(self.cost_delta_pct, 2),
            "by_task": {k: _bucket(v) for k, v in self.by_task.items()},
            "by_channel": {k: _bucket(v) for k, v in self.by_channel.items()},
            "actual_provider_counts": dict(self.actual_provider_counts),
            "new_provider_counts": dict(self.new_provider_counts),
        }


# --- DB access (synchronous sqlite — keep CLI dep-free) --------------------


def _resolve_db_path(database_url: str) -> Path:
    """Resolve a `sqlite+aiosqlite:///path` URL down to the underlying file."""
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(
            f"router.replay only supports SQLite log databases today; got {database_url!r}"
        )
    return Path(database_url[len(prefix):])


def _load_rows(db_path: Path, since: datetime | None, limit: int | None) -> list[_Row]:
    if not db_path.exists():
        raise FileNotFoundError(f"Log DB not found at {db_path}")
    rows: list[_Row] = []
    conn = sqlite3.connect(str(db_path))
    try:
        sql = (
            "SELECT request_id, ts, task_type, channel, selected_provider, "
            "selected_model, COALESCE(prompt_tokens, 0), COALESCE(completion_tokens, 0), "
            "COALESCE(estimated_usd, 0.0) "
            "FROM request_log WHERE status = 'succeeded'"
        )
        params: list[Any] = []
        if since:
            sql += " AND ts >= ?"
            params.append(since.isoformat(sep=" "))
        sql += " ORDER BY ts DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        cur = conn.execute(sql, params)
        for r in cur.fetchall():
            rows.append(
                _Row(
                    request_id=r[0],
                    ts=r[1],
                    task_type=r[2],
                    channel=r[3],
                    actual_provider=r[4],
                    actual_model=r[5],
                    prompt_tokens=int(r[6]),
                    completion_tokens=int(r[7]),
                    actual_usd=float(r[8]),
                )
            )
    finally:
        conn.close()
    return rows


# --- Replay engine ----------------------------------------------------------


def replay(
    candidate_config: dict,
    pricing: dict,
    rows: list[_Row],
) -> ReplayReport:
    """Re-run each row through a DecisionEngine built from the candidate config."""
    engine = DecisionEngine(
        config=candidate_config,
        cost_engine=CostEngine(pricing=pricing),
    )
    cost_engine = CostEngine(pricing=pricing)

    report = ReplayReport(
        total_rows=len(rows),
        skipped=0,
        actual_total_usd=0.0,
        new_total_usd=0.0,
    )

    by_task: dict[str, _AggBucket] = defaultdict(_AggBucket)
    by_channel: dict[str, _AggBucket] = defaultdict(_AggBucket)
    actual_counts: dict[str, int] = defaultdict(int)
    new_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        if not row.task_type:
            report.skipped += 1
            continue

        task = ClassifiedTask(task_type=row.task_type, reason="(replay)")
        metadata: dict[str, Any] = {}
        if row.channel:
            metadata["channel"] = row.channel
        try:
            decision = engine.decide(task, metadata=metadata or None)
        except Exception:
            report.skipped += 1
            continue

        # Estimate the new cost using the same token counts the actual call
        # used. Token counts are approximately model-independent at routing
        # granularity — the prompt + completion length wouldn't change.
        new_estimate = cost_engine.estimate(
            provider=decision.provider,
            model=decision.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
        )
        new_usd = new_estimate.estimated_usd if new_estimate.pricing_known else row.actual_usd

        report.actual_total_usd += row.actual_usd
        report.new_total_usd += new_usd

        task_bucket = by_task[row.task_type]
        task_bucket.actual_calls += 1
        task_bucket.new_calls += 1
        task_bucket.actual_usd += row.actual_usd
        task_bucket.new_usd += new_usd

        channel_label = row.channel or "untagged"
        ch_bucket = by_channel[channel_label]
        ch_bucket.actual_calls += 1
        ch_bucket.new_calls += 1
        ch_bucket.actual_usd += row.actual_usd
        ch_bucket.new_usd += new_usd

        if row.actual_provider:
            actual_counts[row.actual_provider] += 1
        new_counts[decision.provider] += 1

    report.by_task = dict(by_task)
    report.by_channel = dict(by_channel)
    report.actual_provider_counts = dict(actual_counts)
    report.new_provider_counts = dict(new_counts)
    return report


# --- CLI --------------------------------------------------------------------


def _print_human_report(report: ReplayReport, candidate_path: Path) -> None:
    print(f"=== Routing replay: {candidate_path} vs current DB ===")
    print(
        f"Replayed {report.total_rows - report.skipped} of {report.total_rows} requests"
        f" ({report.skipped} skipped — missing task_type or engine error)"
    )
    print()
    if report.total_rows == 0:
        print("(no rows in log — nothing to replay)")
        return

    delta_sign = "+" if report.cost_delta_usd >= 0 else ""
    print(
        f"Total: ${report.actual_total_usd:.6f}  →  ${report.new_total_usd:.6f}  "
        f"({delta_sign}{report.cost_delta_pct:.1f}%, "
        f"{delta_sign}${report.cost_delta_usd:.6f})"
    )
    print()

    if report.by_task:
        print("By task type:")
        for task, b in sorted(report.by_task.items()):
            d_usd = b.new_usd - b.actual_usd
            d_pct = (100.0 * d_usd / b.actual_usd) if b.actual_usd > 0 else 0.0
            sign = "+" if d_usd >= 0 else ""
            print(
                f"  {task:25s} ${b.actual_usd:.6f} → ${b.new_usd:.6f} "
                f"({sign}{d_pct:.1f}%)"
            )
        print()

    if report.by_channel:
        print("By channel:")
        for ch, b in sorted(report.by_channel.items()):
            d_usd = b.new_usd - b.actual_usd
            d_pct = (100.0 * d_usd / b.actual_usd) if b.actual_usd > 0 else 0.0
            sign = "+" if d_usd >= 0 else ""
            print(
                f"  {ch:25s} ${b.actual_usd:.6f} → ${b.new_usd:.6f} "
                f"({sign}{d_pct:.1f}%)"
            )
        print()

    if report.new_provider_counts or report.actual_provider_counts:
        print("Provider distribution shifts:")
        providers = sorted(
            set(report.actual_provider_counts) | set(report.new_provider_counts)
        )
        for p in providers:
            a = report.actual_provider_counts.get(p, 0)
            n = report.new_provider_counts.get(p, 0)
            delta = n - a
            sign = "+" if delta >= 0 else ""
            print(f"  {p:25s} {a} → {n}  ({sign}{delta})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="router.replay", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", required=True, type=Path, help="Candidate routing config YAML."
    )
    parser.add_argument(
        "--pricing",
        type=Path,
        default=Path("configs/pricing.yaml"),
        help="Pricing table YAML (default: configs/pricing.yaml).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/router.db"),
        help="SQLite request log (default: data/router.db).",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO date or datetime; only replay rows newer than this (UTC).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on rows replayed (most recent first).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    candidate = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    pricing: dict = {}
    if args.pricing.exists():
        pricing = yaml.safe_load(args.pricing.read_text(encoding="utf-8")) or {}

    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    rows = _load_rows(args.db, since=since, limit=args.limit)
    report = replay(candidate, pricing, rows)

    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_human_report(report, args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
