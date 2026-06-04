"""End-to-end tests for the HTTP API via FastAPI's TestClient.

These run the full request path — classifier, decision engine, mock provider —
without spinning up a real server. No network calls, no API keys required.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    # `with TestClient(app)` triggers the lifespan startup hook.
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_name"]
    assert body["environment"]


@pytest.mark.parametrize(
    "content,expected_task,expected_model",
    [
        ("please rewrite this email", "rewrite", "mock-rewrite-model"),
        ("give me a short summary", "summarization", "mock-summary-model"),
        ("return JSON with extract fields", "structured_extraction", "mock-json-model"),
        ("discuss the architecture tradeoffs", "reasoning", "mock-premium-model"),
        ("hello there", "general_chat", "mock-default-model"),
    ],
)
def test_classifier_branches_via_http(
    client: TestClient, content: str, expected_task: str, expected_model: str
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": content}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["routing"]["task_type"] == expected_task
    assert body["model"] == expected_model
    assert body["routing"]["selected_provider"] == "mock"
    assert body["choices"][0]["message"]["content"].startswith("[MOCK RESPONSE")


def test_response_envelope_shape(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    body = response.json()
    # Top-level OpenAI-compatible fields
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    # Usage block
    usage = body["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    # Choices
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["role"] == "assistant"


def test_explicit_model_routes_to_mock(client: TestClient) -> None:
    """Phase 1 safety: client-supplied model is served by the mock provider."""
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["routing"]["selected_provider"] == "mock"
    assert body["routing"]["selected_model"] == "gpt-4o-mini"
    assert body["choices"][0]["message"]["content"].startswith("[MOCK RESPONSE via gpt-4o-mini]")


def test_request_id_header_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "trace-abc"})
    assert response.headers["x-request-id"] == "trace-abc"


def test_request_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["x-request-id"]  # non-empty
    assert len(response.headers["x-request-id"]) >= 8


def test_validation_error_on_missing_messages(client: TestClient) -> None:
    response = client.post("/v1/chat/completions", json={})
    assert response.status_code == 422


def test_cost_block_present_for_known_model(client: TestClient) -> None:
    """Test pricing has mock-rewrite-model => cost should be computed."""
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    body = response.json()
    cost = body["cost"]
    assert cost["pricing_known"] is True
    assert cost["currency"] == "USD"
    # mock-rewrite-model = input 1.0 / output 2.0 per 1M tokens
    assert cost["input_rate_per_million"] == 1.0
    assert cost["output_rate_per_million"] == 2.0
    usage = body["usage"]
    expected = (
        usage["prompt_tokens"] * 1.0 / 1_000_000
        + usage["completion_tokens"] * 2.0 / 1_000_000
    )
    assert cost["estimated_usd"] == pytest.approx(expected)


def test_route_endpoint_returns_decision_without_calling_provider(client: TestClient) -> None:
    """`/v1/chat/route` should return the routing decision but never invoke any provider."""
    response = client.post(
        "/v1/chat/route",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_type"] == "rewrite"
    assert body["selected_provider"] == "mock"
    assert body["selected_model"] == "mock-rewrite-model"
    # The task reason and routing reason should both be populated.
    assert body["task_reason"]
    assert "rewrite_to_mock" in body["reason"]
    assert isinstance(body["fallbacks"], list)
    assert isinstance(body["cache_enabled"], bool)


def test_route_endpoint_explicit_model_short_circuits(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/route",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    body = response.json()
    assert body["selected_provider"] == "manual"
    assert body["selected_model"] == "gpt-4o-mini"


def test_cost_block_uses_wildcard_for_unpriced_model(client: TestClient) -> None:
    """general_chat falls through to default mock-default-model, which hits the '*' rule."""
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    body = response.json()
    cost = body["cost"]
    assert cost["pricing_known"] is True  # matched mock.* in test_pricing.yaml
    assert cost["input_rate_per_million"] == 0.5
    assert cost["output_rate_per_million"] == 0.5
