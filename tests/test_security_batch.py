"""Tests for the Phase 2c security batch.

Covers: dashboard auth (Basic + Bearer), spend circuit-breaker, client model
override toggle, loadable classifier prototypes.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.settings import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --- Dashboard auth ---------------------------------------------------------


def test_dashboard_open_when_dashboard_auth_disabled(client: TestClient) -> None:
    """Default: API_TOKEN set, DASHBOARD_AUTH=false → dashboard still open."""
    settings.api_token = "test-token"
    settings.dashboard_auth = False
    try:
        assert client.get("/dashboard").status_code == 200
        assert client.get("/dashboard/cost").status_code == 200
    finally:
        settings.api_token = ""


def test_dashboard_locked_when_dashboard_auth_enabled(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "tok")
    monkeypatch.setattr(settings, "dashboard_auth", True)
    r = client.get("/dashboard")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers
    assert r.headers["WWW-Authenticate"].startswith("Basic")


def test_dashboard_accepts_basic_auth(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "tok")
    monkeypatch.setattr(settings, "dashboard_auth", True)
    credentials = base64.b64encode(b"anyuser:tok").decode()
    r = client.get("/dashboard", headers={"Authorization": f"Basic {credentials}"})
    assert r.status_code == 200


def test_dashboard_accepts_bearer_when_locked(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "tok")
    monkeypatch.setattr(settings, "dashboard_auth", True)
    r = client.get("/dashboard", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200


def test_cache_stats_locked_with_dashboard_auth(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "api_token", "tok")
    monkeypatch.setattr(settings, "dashboard_auth", True)
    assert client.get("/v1/cache/stats").status_code == 401
    r = client.get("/v1/cache/stats", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200


# --- Spend circuit-breaker --------------------------------------------------


async def _seed_succeeded_log(usd: float) -> None:
    """Write a row directly to request_log to simulate spend during the day."""
    from datetime import datetime, timezone

    from app.db.models import RequestLog
    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            RequestLog(
                request_id="seed",
                ts=datetime.now(timezone.utc),
                endpoint="/v1/chat/completions",
                task_type="rewrite",
                selected_provider="mock",
                selected_model="mock-rewrite-model",
                prompt_tokens=10,
                completion_tokens=5,
                estimated_usd=usd,
                status="succeeded",
                attempts=[],
                error_message=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_circuit_breaker_trips_when_cap_exceeded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_daily_usd", 0.01)
    with TestClient(app) as client:
        # Seed today's log with $0.02 of spend; the cap is $0.01.
        await _seed_succeeded_log(usd=0.02)
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "rewrite this"}]},
        )
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["type"] == "budget_exceeded"
    assert body["error"]["spent_usd"] == pytest.approx(0.02)
    assert body["error"]["cap_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_circuit_breaker_disabled_when_cap_zero(monkeypatch) -> None:
    """max_daily_usd = 0 means no cap."""
    monkeypatch.setattr(settings, "max_daily_usd", 0.0)
    with TestClient(app) as client:
        await _seed_succeeded_log(usd=999.0)
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "rewrite this"}]},
        )
    assert r.status_code == 200


# --- Loadable classifier prototypes -----------------------------------------


def test_classifier_loads_prototypes_from_yaml(monkeypatch, tmp_path) -> None:
    """If `classifier_prototypes_path` resolves to a real file, those examples
    take precedence over the in-code DEFAULT_PROTOTYPES."""
    proto_file = tmp_path / "custom_prototypes.yaml"
    proto_file.write_text(
        "rewrite:\n"
        "  - 'custom rewrite example one'\n"
        "  - 'custom rewrite example two'\n"
        "summarization:\n"
        "  - 'custom summary example'\n"
    )
    monkeypatch.setattr(settings, "classifier_prototypes_path", str(proto_file))
    deps.reset_caches()

    classifier = deps.get_classifier()
    assert "rewrite" in classifier.prototypes
    assert classifier.prototypes["rewrite"] == [
        "custom rewrite example one",
        "custom rewrite example two",
    ]
    assert classifier.prototypes["summarization"] == ["custom summary example"]


def test_invalid_prototypes_yaml_raises_config_error(monkeypatch, tmp_path) -> None:
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("rewrite: not_a_list\n")
    monkeypatch.setattr(settings, "classifier_prototypes_path", str(bad_file))
    deps.reset_caches()

    from router.errors import ConfigError

    with pytest.raises(ConfigError):
        deps.get_classifier()
