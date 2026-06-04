"""Tests for the SSE streaming chat completion path."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _parse_sse(text: str) -> list[dict | str]:
    """Parse the raw `text/event-stream` body into a list of events (dicts or '[DONE]')."""
    events: list[dict | str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            events.append("[DONE]")
            continue
        events.append(json.loads(payload))
    return events


def test_streaming_returns_sse_content_type(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "please rewrite this email"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


def test_streaming_yields_chunks_meta_and_done(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "please rewrite this email"}],
            "stream": True,
        },
    ) as response:
        body = response.read().decode()
    events = _parse_sse(body)

    # Final event is the [DONE] terminator.
    assert events[-1] == "[DONE]"
    # Second-to-last event is our router metadata.
    meta = events[-2]
    assert isinstance(meta, dict)
    assert meta.get("x_smart_router_meta") is True
    assert meta["routing"]["task_type"] == "rewrite"
    assert meta["routing"]["selected_provider"] == "mock"
    assert meta["routing"]["selected_model"] == "mock-rewrite-model"
    assert meta["cost"]["pricing_known"] is True

    # Third-to-last carries finish_reason=stop with empty delta.
    finish = events[-3]
    assert isinstance(finish, dict)
    assert finish["choices"][0]["finish_reason"] == "stop"

    # Earlier events should be chunks with delta.content.
    content_events = [
        e for e in events[:-3]
        if isinstance(e, dict) and e.get("choices") and "content" in (e["choices"][0].get("delta") or {})
    ]
    assert len(content_events) > 0
    combined = "".join(e["choices"][0]["delta"]["content"] for e in content_events)
    assert "MOCK RESPONSE" in combined
    assert "rewrite this email" in combined


def test_streaming_writes_log_row(client: TestClient) -> None:
    """A successful stream should still leave a row in the request log."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "please rewrite this"}],
            "stream": True,
        },
    ) as response:
        response.read()  # drain

    # Dashboard requests page should mention the model from the stream.
    dash = client.get("/dashboard/requests")
    assert dash.status_code == 200
    assert "mock-rewrite-model" in dash.text


def test_non_streaming_still_returns_json(client: TestClient) -> None:
    """Regression: stream=false (default) keeps the JSON response shape."""
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["routing"]["selected_provider"] == "mock"
