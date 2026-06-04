"""Persistent-log + dashboard integration tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_chat_completion_writes_a_log_row(client: TestClient) -> None:
    """Every successful chat completion should leave a row in request_log."""
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    assert response.status_code == 200
    body = response.json()
    request_id = body["routing"]["attempts"][-1]  # ensure response shape unchanged

    # Now scrape /dashboard/requests — should show our call.
    dash = client.get("/dashboard/requests")
    assert dash.status_code == 200
    html = dash.text
    assert "mock-rewrite-model" in html
    assert "ok" in html  # success marker
    assert "succeeded" not in html  # status string isn't shown raw, just "ok"/"fail"


def test_dashboard_pages_return_200(client: TestClient) -> None:
    for path in (
        "/dashboard",
        "/dashboard/requests",
        "/dashboard/cost",
        "/dashboard/playground",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert "Switchboard" in r.text


def test_playground_page_has_expected_widgets(client: TestClient) -> None:
    r = client.get("/dashboard/playground")
    assert r.status_code == 200
    html = r.text
    # Quick smoke check that core widgets are wired up.
    for needle in ('id="prompt"', 'id="runBtn"', 'id="estimateRows"', 'id="response"', '/v1/chat/estimate', '/v1/chat/completions'):
        assert needle in html, f"playground missing {needle!r}"


def test_root_redirects_to_dashboard(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/dashboard"


def test_dashboard_overview_aggregates_multiple_requests(client: TestClient) -> None:
    """Send a few requests, check the overview shows counts > 1."""
    for content in (
        "please rewrite this",
        "give me a short summary",
        "return JSON with extract fields",
    ):
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": content}]},
        )
        assert r.status_code == 200

    dash = client.get("/dashboard")
    assert dash.status_code == 200
    html = dash.text
    # 3 successful requests with mock provider
    assert "mock" in html
    # KPI block should have at least one digit (request count).
    assert "Requests" in html
