"""Bearer-token auth middleware tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_no_auth_required_when_token_unset(client: TestClient) -> None:
    """Default: api_token is empty → no auth enforcement."""
    settings.api_token = ""
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200


def test_protected_route_rejects_missing_token(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["type"] == "unauthorized"


def test_protected_route_rejects_wrong_token(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer not-the-right-one"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


def test_protected_route_accepts_correct_token(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret-test-token"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200


def test_health_bypasses_auth_even_when_token_set(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    response = client.get("/health")
    assert response.status_code == 200


def test_dashboard_bypasses_auth_even_when_token_set(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    response = client.get("/dashboard")
    assert response.status_code == 200


def test_estimate_endpoint_is_also_protected(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "secret-test-token")
    # Missing token.
    r1 = client.post("/v1/chat/estimate", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r1.status_code == 401
    # With token.
    r2 = client.post(
        "/v1/chat/estimate",
        headers={"Authorization": "Bearer secret-test-token"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r2.status_code == 200
