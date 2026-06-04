"""Tests for the pre-flight cost preview endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from router.estimation import estimate_messages_tokens, estimate_tokens


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_estimate_tokens_empty_string() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_grows_with_text() -> None:
    assert estimate_tokens("hello") >= 1
    assert estimate_tokens("hello world") > estimate_tokens("hello")
    assert estimate_tokens("a" * 400) >= 100  # at least ~chars/4


def test_estimate_messages_tokens_sums_all() -> None:
    n = estimate_messages_tokens(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Please rewrite this email."},
        ]
    )
    assert n > 0


def test_estimate_endpoint_uses_config_candidates(client: TestClient) -> None:
    """No explicit candidates → endpoint estimates for every provider+model in config."""
    response = client.post(
        "/v1/chat/estimate",
        json={"messages": [{"role": "user", "content": "Hi please rewrite this."}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["input_tokens_estimated"] > 0
    assert body["assumed_max_completion_tokens"] == 256
    estimates = body["estimates"]
    assert len(estimates) >= 1
    # Test config uses mock provider; mock-rewrite-model is priced in test_pricing.yaml.
    by_model = {e["model"]: e for e in estimates}
    assert "mock-rewrite-model" in by_model
    rewrite = by_model["mock-rewrite-model"]
    assert rewrite["pricing_known"] is True
    assert rewrite["estimated_usd_min"] < rewrite["estimated_usd_max"]


def test_estimate_endpoint_explicit_candidates(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/estimate",
        json={
            "messages": [{"role": "user", "content": "A short test prompt."}],
            "candidates": [
                {"provider": "mock", "model": "mock-rewrite-model"},
                {"provider": "mock", "model": "mock-summary-model"},
            ],
            "assumed_max_tokens": 100,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assumed_max_completion_tokens"] == 100
    assert len(body["estimates"]) == 2
    # mock-summary-model costs more per output token in test_pricing.yaml (4.0 vs 2.0).
    by_model = {e["model"]: e for e in body["estimates"]}
    summary = by_model["mock-summary-model"]
    rewrite = by_model["mock-rewrite-model"]
    assert summary["estimated_usd_max"] > rewrite["estimated_usd_max"]


def test_estimate_endpoint_picks_cheapest_and_most_expensive(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/estimate",
        json={
            "messages": [{"role": "user", "content": "A short test prompt."}],
            "candidates": [
                {"provider": "mock", "model": "mock-rewrite-model"},
                {"provider": "mock", "model": "mock-summary-model"},
            ],
        },
    )
    body = response.json()
    assert body["cheapest"]["model"] == "mock-rewrite-model"
    assert body["most_expensive"]["model"] == "mock-summary-model"


def test_estimate_endpoint_unknown_pricing(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/estimate",
        json={
            "messages": [{"role": "user", "content": "test"}],
            "candidates": [{"provider": "no-such-provider", "model": "no-model"}],
        },
    )
    body = response.json()
    only = body["estimates"][0]
    assert only["pricing_known"] is False
    assert only["estimated_usd_max"] is None
    # No priced estimates → no cheapest/most_expensive.
    assert body["cheapest"] is None
    assert body["most_expensive"] is None
