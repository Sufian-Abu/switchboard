"""Follow-up security tests from the round-2 review.

Two things to prove paranoid-strict:
  1. Client-supplied `model:` is rejected on EVERY endpoint when the toggle
     is off (default). It's accepted (via mock provider) when the toggle is on.
  2. `max_cost_per_call` does NOT let unknown-pricing candidates through by
     default (the historical bug).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import app
from router.costing import CostEngine
from router.preference import PreferenceSpec, select_chain


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --- Model override blocking across every endpoint --------------------------


def test_completions_rejects_client_model_by_default(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "model_override_disabled"


def test_completions_stream_rejects_client_model_by_default(client: TestClient) -> None:
    """SSE entry point must enforce the policy before any chunks are emitted."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 400
        body = r.read()
        assert b"model_override_disabled" in body


def test_route_endpoint_rejects_client_model_by_default(client: TestClient) -> None:
    """Defence in depth: /v1/chat/route should not even *preview* a routing
    decision for a client-supplied model when overrides are disabled."""
    r = client.post(
        "/v1/chat/route",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "model_override_disabled"


def test_compare_does_not_accept_a_model_field(client: TestClient) -> None:
    """`/v1/chat/compare` works with explicit candidates, not a `model:` field.
    Confirm that the schema rejects an unexpected `model` key.

    Pydantic v2 ignores unknown fields by default (extra='ignore'), so the model
    field is silently dropped — meaning there's no override path to exploit here.
    This test pins that behaviour so a future schema change doesn't regress it.
    """
    r = client.post(
        "/v1/chat/compare",
        json={
            "model": "gpt-4o",  # ignored — `ChatCompareRequest` has no `model` field
            "messages": [{"role": "user", "content": "hi"}],
            "candidates": [{"provider": "mock", "model": "mock-rewrite-model"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Only the explicitly-listed candidate ran. No `gpt-4o` anywhere.
    assert all(result["model"] == "mock-rewrite-model" for result in body["results"])


# --- The legacy path still works when explicitly enabled -------------------


def test_completions_accepts_client_model_when_override_enabled(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setattr(settings, "allow_client_model_override", True)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert r.json()["routing"]["selected_provider"] == "mock"


def test_route_endpoint_accepts_client_model_when_override_enabled(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setattr(settings, "allow_client_model_override", True)
    r = client.post(
        "/v1/chat/route",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["selected_provider"] == "manual"
    assert body["selected_model"] == "gpt-4o-mini"


# --- Pricing-bypass fix: unknown pricing must NOT pass under a cap ---------


def test_max_cost_per_call_blocks_unknown_pricing_by_default() -> None:
    """Round-2 review pointed out: a model with no `pricing.yaml` entry was
    sneaking past `max_cost_per_call`. Confirm we now fail closed."""
    engine = CostEngine(pricing={"cloud": {"priced": {"input": 0.05, "output": 0.05}}})
    spec = PreferenceSpec(policy="manual", max_cost_per_call=0.001)
    candidates = [
        ("cloud", "priced"),       # estimated under cap
        ("unknown", "no-pricing"),  # would have slipped through historically
    ]
    chain = select_chain(candidates, spec, engine)
    # Only the priced-under-cap candidate survives.
    assert chain == [("cloud", "priced")]


def test_max_cost_per_call_can_opt_back_into_legacy_behaviour() -> None:
    """Trusted local providers (e.g. Ollama) can opt back into the permissive
    behaviour via `allow_unknown_pricing_under_cap: true`."""
    engine = CostEngine(pricing={"cloud": {"priced": {"input": 0.05, "output": 0.05}}})
    spec = PreferenceSpec(
        policy="manual",
        max_cost_per_call=0.001,
        allow_unknown_pricing_under_cap=True,
    )
    candidates = [
        ("cloud", "priced"),
        ("ollama", "llama3.2:1b"),  # unknown pricing — opt-in lets it pass
    ]
    chain = select_chain(candidates, spec, engine)
    # Both survive.
    assert set(chain) == {("cloud", "priced"), ("ollama", "llama3.2:1b")}


def test_pricing_bypass_blocked_via_decision_engine_end_to_end() -> None:
    """A routing rule with `max_cost_per_call` should fall through to the next
    rule if its only candidates are unknown-pricing (default-secure)."""
    from router.decision_engine import DecisionEngine
    from router.schemas import ClassifiedTask

    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "capped_with_unknown_only",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "uncharted", "model": "no-price"},
                    "max_cost_per_call": 0.001,
                },
                {
                    "name": "safe_fallback",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "mock", "model": "mock-rewrite-model"},
                },
            ]
        },
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))

    assert decision.provider == "mock"
    assert decision.model == "mock-rewrite-model"
    assert "safe_fallback" in decision.reason
