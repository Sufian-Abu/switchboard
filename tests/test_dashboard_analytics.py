"""Tests for the Batch A dashboard analytics additions.

Specifically:
  - The new `channel` column on RequestLog is populated from request.metadata.
  - The new `routing_reason` column is populated from the decision.
  - The cost page renders the per-channel breakdown.
  - The requests page shows the routing-reason cell.
  - The overview page shows the task-type breakdown table.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_channel_metadata_is_persisted_to_request_log(client: TestClient) -> None:
    """When the client sends metadata.channel, it should land in the log row
    so the per-channel breakdown is queryable."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "please rewrite this email"}],
            "metadata": {"channel": "whatsapp"},
        },
    )
    assert r.status_code == 200

    # The requests page should show the channel for the most recent row.
    page = client.get("/dashboard/requests").text
    # We expect "whatsapp" rendered somewhere in the table (in a `<td>`).
    assert "whatsapp" in page


def test_routing_reason_is_persisted_and_rendered(client: TestClient) -> None:
    """The /dashboard/requests page should surface the routing reason."""
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    assert r.status_code == 200
    body = r.json()
    expected_reason = body["routing"]["reason"]

    page = client.get("/dashboard/requests").text
    # The expandable reason cell carries the exact reason text from the response.
    assert expected_reason in page


def test_overview_shows_task_type_breakdown_table(client: TestClient) -> None:
    """After at least one request, the overview should show task-type rows."""
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    page = client.get("/dashboard").text
    assert "Task type breakdown" in page
    assert "rewrite" in page


def test_cost_page_shows_channel_breakdown(client: TestClient) -> None:
    client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "please rewrite this email"}],
            "metadata": {"channel": "whatsapp"},
        },
    )
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},  # no channel
    )
    page = client.get("/dashboard/cost").text
    assert "By channel" in page
    assert "whatsapp" in page
    assert "untagged" in page  # row for requests without a channel
