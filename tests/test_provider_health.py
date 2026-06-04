"""Tests for the provider health tracker + the `avoid_if_health` rule."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.health import ProviderHealthTracker, _BandThresholds
from router.schemas import ClassifiedTask


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --- Tracker unit tests -----------------------------------------------------


def test_tracker_reports_healthy_with_no_data() -> None:
    t = ProviderHealthTracker()
    snap = t.snapshot("nobody")
    assert snap.band == "healthy"
    assert snap.sample_size == 0


def test_tracker_reports_healthy_below_min_samples() -> None:
    t = ProviderHealthTracker(thresholds=_BandThresholds(min_samples=10))
    for _ in range(3):
        t.record("groq", success=False, latency_ms=100)
    # Only 3 samples → below min_samples → healthy regardless of error rate.
    assert t.snapshot("groq").band == "healthy"


def test_tracker_classifies_degraded_on_high_error_rate() -> None:
    t = ProviderHealthTracker(
        thresholds=_BandThresholds(
            min_samples=5, error_rate_degraded=0.1, error_rate_unhealthy=0.5
        )
    )
    # 8 successes, 2 failures = 20% error → degraded.
    for _ in range(8):
        t.record("groq", success=True, latency_ms=100)
    for _ in range(2):
        t.record("groq", success=False, latency_ms=100)
    assert t.snapshot("groq").band == "degraded"


def test_tracker_classifies_unhealthy_on_high_latency() -> None:
    t = ProviderHealthTracker(
        thresholds=_BandThresholds(
            min_samples=5, latency_p95_degraded_ms=200, latency_p95_unhealthy_ms=500
        )
    )
    # All successes but high latency → unhealthy based on p95.
    for latency in [600, 700, 800, 900, 1000, 1100, 1200, 1300]:
        t.record("ollama", success=True, latency_ms=latency)
    snap = t.snapshot("ollama")
    assert snap.band == "unhealthy"
    assert snap.latency_p95_ms >= 1000


def test_is_avoidable_threshold_semantics() -> None:
    """`is_avoidable(threshold)` is True when the current band is at-or-worse than threshold."""
    t = ProviderHealthTracker(thresholds=_BandThresholds(min_samples=2))
    for _ in range(8):
        t.record("groq", success=True, latency_ms=100)
    for _ in range(2):
        t.record("groq", success=False, latency_ms=100)
    snap = t.snapshot("groq")
    assert snap.band == "degraded"
    # Threshold 'degraded' avoids degraded+unhealthy.
    assert snap.is_avoidable("degraded") is True
    # Threshold 'unhealthy' only avoids unhealthy.
    assert snap.is_avoidable("unhealthy") is False


# --- DecisionEngine integration --------------------------------------------


def test_decision_engine_avoids_degraded_provider() -> None:
    """A rule with `avoid_if_health: degraded` should skip a provider whose
    rolling stats put it in the degraded band."""
    tracker = ProviderHealthTracker(thresholds=_BandThresholds(min_samples=5))
    # Mark "broken_provider" as degraded.
    for _ in range(8):
        tracker.record("broken_provider", success=False, latency_ms=100)
    for _ in range(2):
        tracker.record("broken_provider", success=True, latency_ms=100)

    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_with_health_filter",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "broken_provider", "model": "x"},
                    "fallbacks": [
                        {"provider": "mock", "model": "mock-rewrite-model"},
                    ],
                    "avoid_if_health": "degraded",
                }
            ]
        },
    }
    engine = DecisionEngine(
        config=config,
        cost_engine=CostEngine(pricing={}),
        health_tracker=tracker,
    )
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))
    assert decision.provider == "mock"
    assert decision.model == "mock-rewrite-model"
    assert "avoid_if_health=degraded" in decision.reason


def test_decision_engine_falls_through_when_all_candidates_unhealthy() -> None:
    tracker = ProviderHealthTracker(thresholds=_BandThresholds(min_samples=5))
    for _ in range(10):
        tracker.record("broken", success=False, latency_ms=100)

    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "all_broken",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "broken", "model": "x"},
                    "avoid_if_health": "degraded",
                },
                {
                    "name": "safe_fallback",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "mock", "model": "mock-rewrite-model"},
                },
            ]
        },
    }
    engine = DecisionEngine(
        config=config,
        cost_engine=CostEngine(pricing={}),
        health_tracker=tracker,
    )
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))
    assert decision.model == "mock-rewrite-model"
    assert "safe_fallback" in decision.reason


def test_decision_engine_rejects_invalid_avoid_if_health() -> None:
    tracker = ProviderHealthTracker()
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "typo_rule",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "mock", "model": "x"},
                    "avoid_if_health": "kinda-broken",  # not a real band
                }
            ]
        },
    }
    engine = DecisionEngine(config=config, health_tracker=tracker)
    with pytest.raises(ValueError, match="invalid `avoid_if_health`"):
        engine.decide(ClassifiedTask(task_type="rewrite", reason=""))


# --- HTTP integration -------------------------------------------------------


def test_health_endpoint_returns_per_provider_stats(client: TestClient) -> None:
    """After driving traffic, `/v1/health/providers` should report at least one provider."""
    # Send a few requests.
    for content in ("please rewrite this", "give me a short summary", "hello there"):
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": content}]},
        )
        assert r.status_code == 200

    r = client.get("/v1/health/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["window_seconds"] > 0
    providers = body["providers"]
    assert any(p["provider"] == "mock" for p in providers)
    mock_row = next(p for p in providers if p["provider"] == "mock")
    assert mock_row["band"] == "healthy"
    assert mock_row["sample_size"] >= 3
