"""Tests for the parallel compare-all endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_compare_returns_one_result_per_candidate(client: TestClient) -> None:
    """Explicit candidates → one result per entry, all using MockProvider."""
    response = client.post(
        "/v1/chat/compare",
        json={
            "messages": [{"role": "user", "content": "please rewrite this email"}],
            "candidates": [
                {"provider": "mock", "model": "mock-rewrite-model"},
                {"provider": "mock", "model": "mock-summary-model"},
                {"provider": "mock", "model": "mock-default-model"},
            ],
            "max_tokens": 50,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["results"]) == 3
    for r in body["results"]:
        assert r["status"] == "succeeded"
        assert r["content"].startswith("[MOCK RESPONSE")
        assert r["latency_ms"] >= 0
        assert r["pricing_known"] is True


def test_compare_marks_cheapest_and_fastest(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/compare",
        json={
            "messages": [{"role": "user", "content": "test"}],
            "candidates": [
                {"provider": "mock", "model": "mock-rewrite-model"},  # 1.0 in / 2.0 out
                {"provider": "mock", "model": "mock-summary-model"},  # 3.0 in / 4.0 out
            ],
            "max_tokens": 50,
        },
    )
    body = response.json()
    assert body["cheapest"]["model"] == "mock-rewrite-model"
    assert body["fastest"] is not None  # picks one; both are fast
    assert body["total_estimated_usd"] > 0


def test_compare_uses_config_candidates_when_omitted(client: TestClient) -> None:
    """No candidates → use every (provider, model) in the test routing config."""
    response = client.post(
        "/v1/chat/compare",
        json={
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 30,
        },
    )
    body = response.json()
    assert len(body["results"]) >= 1
    # Test config only uses mock provider, so all succeed.
    assert all(r["status"] == "succeeded" for r in body["results"])


def test_compare_handles_failed_candidate(monkeypatch, client: TestClient) -> None:
    """One failing candidate doesn't abort the whole batch."""
    from app.api import deps
    from router.errors import ProviderError
    from router.providers.base import BaseProvider
    from router.providers.mock_provider import MockProvider

    class _BrokenProvider(BaseProvider):
        name = "broken"

        async def chat(self, model, messages, temperature=0.7, max_tokens=None):
            raise ProviderError("broken", "simulated", status_code=502, upstream_status=503)

        async def stream(self, model, messages, temperature=0.7, max_tokens=None):
            raise NotImplementedError
            yield  # noqa

    def fake_get_provider(name):
        if name == "broken":
            return _BrokenProvider()
        if name == "mock":
            return MockProvider()
        raise ValueError(name)

    monkeypatch.setattr(deps, "get_provider", fake_get_provider)
    monkeypatch.setattr("app.services.compare_service.get_provider", fake_get_provider)

    response = client.post(
        "/v1/chat/compare",
        json={
            "messages": [{"role": "user", "content": "test"}],
            "candidates": [
                {"provider": "mock", "model": "mock-rewrite-model"},
                {"provider": "broken", "model": "anything"},
            ],
        },
    )
    body = response.json()
    assert len(body["results"]) == 2
    statuses = {r["provider"]: r["status"] for r in body["results"]}
    assert statuses["mock"] == "succeeded"
    assert statuses["broken"] == "failed"
    assert body["cheapest"]["provider"] == "mock"  # broken isn't priced
