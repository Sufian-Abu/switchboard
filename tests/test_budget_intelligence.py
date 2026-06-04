"""Tests for the daily soft-cap and burn-rate widget."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import app


async def _seed_succeeded_log(usd: float) -> None:
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
async def test_soft_cap_reorders_chain_when_engaged(monkeypatch) -> None:
    """When today's spend is past the soft-cap fraction, the decision engine
    reorders fallback chains cheapest-first regardless of the rule's normal
    preference."""
    monkeypatch.setattr(settings, "max_daily_usd", 1.0)
    monkeypatch.setattr(settings, "daily_soft_cap_pct", 0.5)

    # Use a custom config: primary is "expensive", fallback is "cheap".
    # No `prefer: cheapest` set — the rule would normally keep the original
    # order. Soft cap should flip it.
    from router.costing import CostEngine
    from router.decision_engine import DecisionEngine
    from router.schemas import ClassifiedTask

    pricing = {
        "cloud": {
            "expensive": {"input": 10.0, "output": 30.0},
            "cheap":     {"input": 0.10, "output": 0.30},
        }
    }
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "no_pref_rule",
                    "when": {"task_type": "rewrite"},
                    "use":       {"provider": "cloud", "model": "expensive"},
                    "fallbacks": [{"provider": "cloud", "model": "cheap"}],
                }
            ]
        },
    }
    engine = DecisionEngine(
        config=config,
        cost_engine=CostEngine(pricing=pricing),
    )

    # Without soft cap → keeps original order.
    d_normal = engine.decide(
        ClassifiedTask(task_type="rewrite", reason=""),
        budget_soft_cap=False,
    )
    assert d_normal.model == "expensive"

    # With soft cap engaged → flips to cheapest.
    d_softcap = engine.decide(
        ClassifiedTask(task_type="rewrite", reason=""),
        budget_soft_cap=True,
    )
    assert d_softcap.model == "cheap"
    assert "soft-cap engaged" in d_softcap.reason


@pytest.mark.asyncio
async def test_soft_cap_engages_after_threshold_via_http(monkeypatch) -> None:
    """End-to-end: seed today's spend past the soft cap; the next request
    routes through a cheaper fallback even without `prefer: cheapest`."""
    monkeypatch.setattr(settings, "max_daily_usd", 0.001)
    monkeypatch.setattr(settings, "daily_soft_cap_pct", 0.5)  # 50% = $0.0005

    # We need a config with multiple priced candidates so reordering is observable.
    # Easiest: override the cached engine via monkeypatch.
    from app.api import deps
    from router.costing import CostEngine
    from router.decision_engine import DecisionEngine

    pricing = {
        "mock": {
            "mock-expensive": {"input": 100.0, "output": 100.0},
            "mock-cheap":     {"input": 0.01,  "output": 0.01},
        }
    }
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "softcap_demo",
                    "when": {"task_type": "rewrite"},
                    "use":       {"provider": "mock", "model": "mock-expensive"},
                    "fallbacks": [{"provider": "mock", "model": "mock-cheap"}],
                }
            ]
        },
    }

    def fake_engine():
        return DecisionEngine(
            config=config,
            cost_engine=CostEngine(pricing=pricing),
        )

    monkeypatch.setattr(deps, "get_decision_engine", fake_engine)
    monkeypatch.setattr("app.services.chat_service.get_decision_engine", fake_engine)
    monkeypatch.setattr(deps, "get_cost_engine", lambda: CostEngine(pricing=pricing))
    monkeypatch.setattr("app.services.chat_service.get_cost_engine", lambda: CostEngine(pricing=pricing))

    with TestClient(app) as client:
        # Seed today's spend above the soft cap (0.0005).
        await _seed_succeeded_log(usd=0.0007)
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "please rewrite this"}]},
        )
    assert r.status_code == 200
    body = r.json()
    # Soft cap engaged → cheapest candidate wins.
    assert body["routing"]["selected_model"] == "mock-cheap"
    assert "soft-cap engaged" in body["routing"]["reason"].lower()


def test_burn_rate_widget_appears_when_cap_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_daily_usd", 1.0)
    with TestClient(app) as client:
        page = client.get("/dashboard").text
    assert "Today's spend" in page
    assert "Projected EOD" in page


def test_burn_rate_widget_absent_when_cap_zero(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_daily_usd", 0.0)
    with TestClient(app) as client:
        page = client.get("/dashboard").text
    assert "Today's spend" not in page
