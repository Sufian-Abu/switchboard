"""Tests for the Routing Replay CLI / library."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from router.replay import _Row, replay


def test_replay_aggregates_by_task_and_channel() -> None:
    """Two rows: one rewrite/whatsapp, one summarization/slack. Replay against
    a candidate config that routes both to a cheaper local model."""
    candidate_config = {
        "default": {"provider": "ollama", "model": "llama3.2:1b"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_to_local",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "ollama", "model": "llama3.2:1b"},
                },
                {
                    "name": "summarize_to_local",
                    "when": {"task_type": "summarization"},
                    "use": {"provider": "ollama", "model": "llama3.2:1b"},
                },
            ]
        },
    }
    pricing = {
        "ollama": {"*": {"input": 0.0, "output": 0.0}},
        "groq":   {"llama-3.1-8b-instant": {"input": 0.05, "output": 0.08}},
        "gemini": {"gemini-2.5-flash":     {"input": 0.30, "output": 2.50}},
    }
    rows = [
        _Row(
            request_id="r1", ts="2026-06-04 12:00:00",
            task_type="rewrite", channel="whatsapp",
            actual_provider="groq", actual_model="llama-3.1-8b-instant",
            prompt_tokens=100, completion_tokens=50,
            actual_usd=0.00001,
        ),
        _Row(
            request_id="r2", ts="2026-06-04 12:05:00",
            task_type="summarization", channel="slack",
            actual_provider="gemini", actual_model="gemini-2.5-flash",
            prompt_tokens=200, completion_tokens=80,
            actual_usd=0.000260,
        ),
    ]

    report = replay(candidate_config, pricing, rows)

    assert report.total_rows == 2
    assert report.skipped == 0
    # New config routes everything to Ollama (free). Cost goes to zero.
    assert report.new_total_usd == pytest.approx(0.0)
    assert report.actual_total_usd > 0
    assert report.cost_delta_usd < 0  # we saved money

    # Per-task buckets recorded.
    assert "rewrite" in report.by_task
    assert "summarization" in report.by_task
    assert report.by_task["rewrite"].new_usd == 0.0
    assert report.by_task["summarization"].new_usd == 0.0

    # Per-channel buckets recorded (channels carried through metadata).
    assert "whatsapp" in report.by_channel
    assert "slack" in report.by_channel

    # Provider counts shifted from groq/gemini to ollama.
    assert report.actual_provider_counts == {"groq": 1, "gemini": 1}
    assert report.new_provider_counts == {"ollama": 2}


def test_replay_skips_rows_without_task_type() -> None:
    rows = [
        _Row(
            request_id="r1", ts="x",
            task_type=None, channel=None,
            actual_provider="mock", actual_model="mock",
            prompt_tokens=10, completion_tokens=5,
            actual_usd=0.0,
        ),
    ]
    report = replay({"default": {"provider": "mock", "model": "m"}, "routing": {"rules": []}}, {}, rows)
    assert report.skipped == 1


def test_replay_report_dict_round_trip() -> None:
    rows = [
        _Row(
            request_id="r1", ts="x",
            task_type="rewrite", channel=None,
            actual_provider="mock", actual_model="mock-rewrite-model",
            prompt_tokens=100, completion_tokens=50,
            actual_usd=0.0001,
        ),
    ]
    report = replay(
        {"default": {"provider": "mock", "model": "m"}, "routing": {"rules": []}}, {}, rows
    )
    payload = report.to_dict()
    for key in ("total_rows", "actual_total_usd", "new_total_usd", "cost_delta_usd",
                "by_task", "by_channel", "actual_provider_counts", "new_provider_counts"):
        assert key in payload


# --- CLI integration: SQLite round-trip ------------------------------------


def test_load_rows_from_real_sqlite(tmp_path: Path) -> None:
    """Spin up an in-process SQLite mirror of the schema, seed it, and have
    _load_rows() read it back."""
    from router.replay import _load_rows

    db_path = tmp_path / "router.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT, ts TEXT, endpoint TEXT,
            task_type TEXT, selected_provider TEXT, selected_model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER,
            estimated_usd REAL, status TEXT,
            attempts TEXT, error_message TEXT, channel TEXT,
            routing_reason TEXT, cohort TEXT
        )
    """)
    conn.execute(
        "INSERT INTO request_log (request_id, ts, task_type, selected_provider, "
        "selected_model, prompt_tokens, completion_tokens, estimated_usd, "
        "status, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r-1", "2026-06-04 12:00:00", "rewrite", "groq",
         "llama-3.1-8b-instant", 100, 50, 0.00001, "succeeded", "whatsapp"),
    )
    conn.execute(
        "INSERT INTO request_log (request_id, ts, task_type, selected_provider, "
        "selected_model, prompt_tokens, completion_tokens, estimated_usd, "
        "status, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r-2", "2026-06-04 12:01:00", "rewrite", "groq",
         "llama-3.1-8b-instant", 80, 30, 0.000008, "failed", None),
    )
    conn.commit()
    conn.close()

    rows = _load_rows(db_path, since=None, limit=None)
    # Only the succeeded row is replayed.
    assert len(rows) == 1
    assert rows[0].request_id == "r-1"
    assert rows[0].channel == "whatsapp"
