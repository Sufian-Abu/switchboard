"""Tests for Prompt Risk Guard."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import app
from router import risk
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.schemas import ClassifiedTask


# --- Detector unit tests ----------------------------------------------------


def test_no_triggers_on_clean_prompt() -> None:
    r = risk.assess("please rewrite this email more concisely")
    assert r.triggered is False
    assert r.categories == ()


def test_detects_ssn() -> None:
    r = risk.assess("My SSN is 123-45-6789, please help.")
    assert r.triggered is True
    assert "pii" in r.categories
    assert "ssn" in r.patterns_matched


def test_detects_email_and_phone_as_pii() -> None:
    r = risk.assess("Contact me at john.doe@example.com or +1-555-123-4567.")
    assert r.triggered is True
    assert "pii" in r.categories
    assert "email" in r.patterns_matched


def test_detects_api_key_leak() -> None:
    r = risk.assess("Why doesn't this key work: sk-abc123XYZ456789")
    assert r.triggered is True
    assert "api_key" in r.patterns_matched


def test_detects_medical_advice_seeking() -> None:
    r = risk.assess("Should I take ibuprofen with my prescription medication?")
    assert r.triggered is True
    assert "medical" in r.categories


def test_detects_legal_advice() -> None:
    r = risk.assess("Can I be liable if I share my NDA-protected info?")
    assert r.triggered is True
    assert "legal" in r.categories


def test_detects_financial_advice() -> None:
    r = risk.assess("Should I sell my house to buy crypto?")
    assert r.triggered is True
    assert "financial" in r.categories


def test_assess_messages_aggregates_across_turns() -> None:
    msgs = [
        {"role": "user", "content": "Here's my SSN: 111-22-3333"},
        {"role": "assistant", "content": "thank you"},
    ]
    r = risk.assess_messages(msgs)
    assert r.triggered is True
    assert "pii" in r.categories


# --- DecisionEngine integration --------------------------------------------


def test_engine_routes_to_safe_provider_when_risky() -> None:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "general_with_safe_fallback",
                    "when": {"task_type": "general_chat"},
                    "use":  {"provider": "openai", "model": "gpt-4o"},
                    "safe_provider": "ollama",
                    "safe_model": "llama3.2:1b",
                }
            ]
        },
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    task = ClassifiedTask(task_type="general_chat", reason="")

    # No risk → normal primary.
    assert engine.decide(task, risk_triggered=False).provider == "openai"

    # Risk triggered → safe_provider takes over.
    risky = engine.decide(task, risk_triggered=True)
    assert risky.provider == "ollama"
    assert risky.model == "llama3.2:1b"
    assert "risk override" in risky.reason
    assert risky.cache_enabled is False  # never cache risky prompts


def test_engine_keeps_normal_chain_when_no_safe_provider_configured() -> None:
    """Risk triggered on a rule with no safe_provider → normal routing applies."""
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "no_safe_provider",
                    "when": {"task_type": "general_chat"},
                    "use":  {"provider": "openai", "model": "gpt-4o"},
                }
            ]
        },
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    decision = engine.decide(
        ClassifiedTask(task_type="general_chat", reason=""), risk_triggered=True
    )
    assert decision.provider == "openai"  # unchanged


# --- HTTP integration -------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_risk_block_absent_by_default(client: TestClient) -> None:
    """When ENABLE_RISK_GUARD=false, the `risk` block stays out of the response."""
    settings.enable_risk_guard = False
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    body = response.json()
    assert body.get("risk") is None


def test_risk_block_appears_when_enabled(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "enable_risk_guard", True)
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "My SSN is 123-45-6789, please rewrite this."}]
        },
    )
    body = response.json()
    assert body["risk"]["triggered"] is True
    assert "pii" in body["risk"]["categories"]
    assert "cache_bypassed" in body["risk"]["actions"]


def test_clean_prompt_with_guard_enabled_shows_no_triggers(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setattr(settings, "enable_risk_guard", True)
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "rewrite this email"}]},
    )
    body = response.json()
    assert body["risk"]["triggered"] is False
    assert body["risk"]["actions"] == []
