"""Tests for metadata-based `when:` matching in the DecisionEngine.

These cover the OpenClaw-style integration pattern where the upstream client
attaches `metadata.channel: whatsapp` (or similar) and the engine picks a rule
based on the value.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.schemas import ClassifiedTask


def _engine(rules: list) -> DecisionEngine:
    return DecisionEngine(
        config={
            "default": {"provider": "mock", "model": "mock-default-model"},
            "routing": {"rules": rules},
        },
        cost_engine=CostEngine(pricing={}),
    )


def _task(task_type: str = "general_chat") -> ClassifiedTask:
    return ClassifiedTask(task_type=task_type, reason="test")


def test_metadata_only_rule_matches() -> None:
    engine = _engine(
        [
            {
                "name": "whatsapp_to_local",
                "when": {"metadata.channel": "whatsapp"},
                "use": {"provider": "ollama", "model": "llama3.2:1b"},
            }
        ]
    )
    decision = engine.decide(_task(), metadata={"channel": "whatsapp"})
    assert decision.provider == "ollama"
    assert decision.model == "llama3.2:1b"
    assert "whatsapp_to_local" in decision.reason


def test_metadata_only_rule_misses_falls_to_default() -> None:
    engine = _engine(
        [
            {
                "name": "whatsapp_to_local",
                "when": {"metadata.channel": "whatsapp"},
                "use": {"provider": "ollama", "model": "llama3.2:1b"},
            }
        ]
    )
    decision = engine.decide(_task(), metadata={"channel": "telegram"})
    assert decision.provider == "mock"
    assert decision.model == "mock-default-model"


def test_metadata_combined_with_task_type_anded() -> None:
    engine = _engine(
        [
            {
                "name": "work_slack_reasoning",
                "when": {"task_type": "reasoning", "metadata.channel": "slack"},
                "use": {"provider": "openai", "model": "gpt-4o"},
            }
        ]
    )
    # Both match → rule hits.
    d1 = engine.decide(_task("reasoning"), metadata={"channel": "slack"})
    assert d1.model == "gpt-4o"

    # Right task type, wrong channel → miss.
    d2 = engine.decide(_task("reasoning"), metadata={"channel": "discord"})
    assert d2.provider == "mock"

    # Right channel, wrong task type → miss.
    d3 = engine.decide(_task("rewrite"), metadata={"channel": "slack"})
    assert d3.provider == "mock"


def test_rule_with_metadata_skipped_when_metadata_absent() -> None:
    """If a rule requires metadata.X but the request didn't send it, skip the rule."""
    engine = _engine(
        [
            {
                "name": "needs_metadata",
                "when": {"metadata.workspace": "production"},
                "use": {"provider": "openai", "model": "gpt-4o"},
            },
            {
                "name": "default_chat",
                "when": {"task_type": "general_chat"},
                "use": {"provider": "groq", "model": "llama-3.1-8b-instant"},
            },
        ]
    )
    decision = engine.decide(_task("general_chat"), metadata=None)
    assert decision.model == "llama-3.1-8b-instant"
    assert "default_chat" in decision.reason


def test_unknown_when_key_fails_closed() -> None:
    """Typos in `when:` keys must not silently match everything."""
    engine = _engine(
        [
            {
                "name": "typo_rule",
                "when": {"task-type": "rewrite"},  # wrong key (hyphen, not underscore)
                "use": {"provider": "openai", "model": "gpt-4o"},
            }
        ]
    )
    decision = engine.decide(_task("rewrite"))
    # Should NOT match → falls through to default.
    assert decision.provider == "mock"


# --- End-to-end via HTTP -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_request_metadata_flows_into_routing(monkeypatch, client: TestClient) -> None:
    """Posting metadata.channel to /v1/chat/completions should affect routing."""
    from app.api import deps
    from router.providers.mock_provider import MockProvider

    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "whatsapp_routing",
                    "when": {"metadata.channel": "whatsapp"},
                    "use": {"provider": "mock", "model": "mock-whatsapp-model"},
                }
            ]
        },
    }

    monkeypatch.setattr(
        deps,
        "get_decision_engine",
        lambda: DecisionEngine(config=config, cost_engine=CostEngine(pricing={})),
    )
    monkeypatch.setattr(
        "app.services.chat_service.get_decision_engine",
        lambda: DecisionEngine(config=config, cost_engine=CostEngine(pricing={})),
    )
    monkeypatch.setattr(deps, "get_provider", lambda name: MockProvider())
    monkeypatch.setattr("app.services.chat_service.get_provider", lambda name: MockProvider())

    r = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"channel": "whatsapp"},
        },
    )
    body = r.json()
    assert body["routing"]["selected_model"] == "mock-whatsapp-model"
    assert "whatsapp_routing" in body["routing"]["reason"]
