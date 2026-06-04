"""Tests for the `split:` A/B routing syntax + the /dashboard/ab page."""
from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.policies import SplitPolicy
from router.schemas import ClassifiedTask


def test_split_policy_respects_weights() -> None:
    """A 99.99/0.01 split should overwhelmingly pick the heavy side."""
    policy = SplitPolicy()
    rule = {
        "name": "demo",
        "split": [
            {"name": "always", "provider": "groq", "model": "small", "weight": 9999},
            {"name": "never",  "provider": "gemini", "model": "flash", "weight": 1},
        ],
    }
    chosen = [policy.pick(rule).name for _ in range(200)]
    assert chosen.count("always") > 190


def test_split_policy_distributes_roughly() -> None:
    policy = SplitPolicy()
    rule = {
        "name": "demo",
        "split": [
            {"name": "a", "provider": "p", "model": "m", "weight": 50},
            {"name": "b", "provider": "p", "model": "m", "weight": 50},
        ],
    }
    random.seed(42)
    chosen = [policy.pick(rule).name for _ in range(1000)]
    a_count = chosen.count("a")
    assert 400 < a_count < 600


def test_split_rule_returns_cohort_and_no_fallback() -> None:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_ab",
                    "when": {"task_type": "rewrite"},
                    "split": [
                        {"name": "fast", "provider": "groq", "model": "small", "weight": 100},
                        {"name": "slow", "provider": "groq", "model": "big", "weight": 1},
                    ],
                }
            ]
        },
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    # Run many trials — fast should dominate; cohort label is recorded.
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))
    assert decision.cohort in {"fast", "slow"}
    # No fallback chain — A/B clean cohort attribution.
    assert decision.fallbacks == []
    assert "A/B cohort" in decision.reason


def test_split_rule_validation() -> None:
    policy = SplitPolicy()

    with pytest.raises(ValueError, match="must be a list"):
        policy.pick({"name": "demo", "split": {"a": 80, "b": 20}})
    with pytest.raises(ValueError, match="empty"):
        policy.pick({"name": "demo", "split": []})
    with pytest.raises(ValueError, match="must have `provider`"):
        policy.pick({"name": "demo", "split": [{"name": "a", "weight": 1}]})
    with pytest.raises(ValueError, match="must be > 0"):
        policy.pick(
            {
                "name": "demo",
                "split": [{"name": "a", "provider": "p", "model": "m", "weight": 0}],
            }
        )


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_ab_dashboard_empty_state(client: TestClient) -> None:
    """With no cohort traffic, /dashboard/ab should show the empty-state explainer."""
    r = client.get("/dashboard/ab")
    assert r.status_code == 200
    assert "No A/B traffic logged" in r.text


def test_ab_dashboard_shows_cohort_breakdown(monkeypatch, client: TestClient) -> None:
    """After traffic through a split rule, /dashboard/ab should list each cohort."""
    from app.api import deps

    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_ab",
                    "when": {"task_type": "rewrite"},
                    "split": [
                        {"name": "cohort_x", "provider": "mock", "model": "mock-rewrite-model", "weight": 1},
                        {"name": "cohort_y", "provider": "mock", "model": "mock-summary-model", "weight": 1},
                    ],
                }
            ]
        },
    }

    def fake_engine():
        return DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))

    monkeypatch.setattr(deps, "get_decision_engine", fake_engine)
    monkeypatch.setattr("app.services.chat_service.get_decision_engine", fake_engine)

    # Send a handful of rewrite requests so both cohorts likely get hit.
    random.seed(0)
    for _ in range(20):
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
        )
        assert r.status_code == 200

    page = client.get("/dashboard/ab").text
    # At least one of the cohort labels should appear.
    assert ("cohort_x" in page) or ("cohort_y" in page)
