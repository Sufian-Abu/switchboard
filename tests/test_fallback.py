"""End-to-end tests for the fallback chain.

Strategy: patch `deps.get_provider` so the "primary" provider raises a
ProviderError (retryable or not, parameterized), then assert the fallback chain
behaves correctly.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from router.decision_engine import DecisionEngine
from router.errors import ProviderError
from router.providers.base import BaseProvider
from router.providers.mock_provider import MockProvider
from router.schemas import ProviderResponse


class _FailingProvider(BaseProvider):
    """Always raises ProviderError. Used to force fallback."""

    name = "failing"

    def __init__(self, upstream_status: int | None = 429) -> None:
        self._upstream_status = upstream_status

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        raise ProviderError(
            self.name,
            f"simulated failure (upstream={self._upstream_status})",
            status_code=502 if self._upstream_status else 503,
            upstream_status=self._upstream_status,
        )

    async def stream(self, model, messages, temperature=0.7, max_tokens=None):
        # Streaming path isn't exercised by these tests; raise immediately.
        raise ProviderError(
            self.name,
            f"simulated stream failure (upstream={self._upstream_status})",
            status_code=502 if self._upstream_status else 503,
            upstream_status=self._upstream_status,
        )
        yield  # noqa: keep this a generator


@pytest.fixture
def fallback_config() -> dict[str, Any]:
    return {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_with_fallback",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "failing", "model": "primary-model"},
                    "fallbacks": [
                        {"provider": "mock", "model": "mock-fallback-model"},
                    ],
                },
                {
                    "name": "rewrite_chain_all_fail",
                    "when": {"task_type": "summarization"},
                    "use": {"provider": "failing", "model": "primary"},
                    "fallbacks": [
                        {"provider": "failing", "model": "fb1"},
                    ],
                },
            ]
        },
    }


@pytest.fixture
def client_with_chain(monkeypatch, fallback_config):
    """Build a TestClient where the decision engine + provider factory use our fixtures."""

    def fake_get_decision_engine() -> DecisionEngine:
        return DecisionEngine(config=fallback_config)

    def fake_get_provider(name: str) -> BaseProvider:
        if name == "failing":
            return _FailingProvider(upstream_status=429)
        if name == "failing_4xx":
            return _FailingProvider(upstream_status=400)
        if name == "mock":
            return MockProvider()
        raise ValueError(f"unexpected provider {name}")

    monkeypatch.setattr(deps, "get_decision_engine", fake_get_decision_engine)
    monkeypatch.setattr(deps, "get_provider", fake_get_provider)
    monkeypatch.setattr(
        "app.services.chat_service.get_decision_engine", fake_get_decision_engine
    )
    monkeypatch.setattr("app.services.chat_service.get_provider", fake_get_provider)

    with TestClient(app) as c:
        yield c


def test_falls_back_to_secondary_when_primary_returns_429(client_with_chain):
    """Primary returns 429 → engine retries the fallback → success."""
    response = client_with_chain.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    routing = body["routing"]
    # Final selection is the fallback that succeeded.
    assert routing["selected_provider"] == "mock"
    assert routing["selected_model"] == "mock-fallback-model"
    # Reason explains the recovery.
    assert "fallback" in routing["reason"].lower()
    # Attempts list shows both steps.
    attempts = routing["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["provider"] == "failing"
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["upstream_status"] == 429
    assert attempts[1]["provider"] == "mock"
    assert attempts[1]["status"] == "succeeded"


def test_all_attempts_fail_returns_502(client_with_chain):
    """If every link in the chain fails with a retryable error, surface the last one."""
    response = client_with_chain.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "give me a short summary"}]},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["type"] == "provider_error"
    assert body["error"]["provider"] == "failing"


def test_non_retryable_error_does_not_trigger_fallback(monkeypatch, fallback_config):
    """A 4xx (bad request / auth) error should NOT advance to the fallback."""

    # Override fallback config: primary is the 4xx-failing provider so fallback would be mock.
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_4xx_no_fallback",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "failing_4xx", "model": "primary"},
                    "fallbacks": [{"provider": "mock", "model": "mock-fb"}],
                }
            ]
        },
    }

    def fake_get_decision_engine() -> DecisionEngine:
        return DecisionEngine(config=config)

    def fake_get_provider(name: str) -> BaseProvider:
        if name == "failing_4xx":
            return _FailingProvider(upstream_status=400)
        if name == "mock":
            return MockProvider()
        raise ValueError(f"unexpected provider {name}")

    monkeypatch.setattr(deps, "get_decision_engine", fake_get_decision_engine)
    monkeypatch.setattr(deps, "get_provider", fake_get_provider)
    monkeypatch.setattr(
        "app.services.chat_service.get_decision_engine", fake_get_decision_engine
    )
    monkeypatch.setattr("app.services.chat_service.get_provider", fake_get_provider)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "please rewrite this"}]},
        )

    # Should NOT have fallen back. 4xx is not retryable.
    assert response.status_code == 502


def test_single_provider_succeeds_no_fallback_in_attempts(client):
    """Regression: a normal (no-fallback) rule still works and has exactly one attempt."""
    # Uses default test_config.yaml — no fallback configured for general_chat.
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    assert response.status_code == 200
    body = response.json()
    attempts = body["routing"]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c
