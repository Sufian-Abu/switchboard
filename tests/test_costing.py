"""Unit tests for the CostEngine."""
from __future__ import annotations

import pytest

from router.costing import CostEngine


@pytest.fixture
def engine() -> CostEngine:
    return CostEngine(
        pricing={
            "groq": {
                "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
            },
            "ollama": {"*": {"input": 0.0, "output": 0.0}},
        }
    )


def test_exact_match_computes_cost(engine: CostEngine) -> None:
    estimate = engine.estimate(
        provider="groq",
        model="llama-3.1-8b-instant",
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
    )
    # 1M input tokens at $0.05/M = $0.05; 0.5M output at $0.08/M = $0.04. Total $0.09.
    assert estimate.input_usd == pytest.approx(0.05)
    assert estimate.output_usd == pytest.approx(0.04)
    assert estimate.estimated_usd == pytest.approx(0.09)
    assert estimate.input_rate_per_million == 0.05
    assert estimate.output_rate_per_million == 0.08
    assert estimate.pricing_known is True


def test_wildcard_model_falls_back(engine: CostEngine) -> None:
    estimate = engine.estimate(
        provider="ollama",
        model="anything-goes",
        prompt_tokens=100,
        completion_tokens=100,
    )
    assert estimate.estimated_usd == 0.0
    assert estimate.pricing_known is True


def test_unknown_provider_returns_unknown(engine: CostEngine) -> None:
    estimate = engine.estimate(
        provider="unknown-provider",
        model="anything",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert estimate.estimated_usd is None
    assert estimate.input_usd is None
    assert estimate.output_usd is None
    assert estimate.pricing_known is False


def test_unknown_model_with_no_wildcard_returns_unknown(engine: CostEngine) -> None:
    estimate = engine.estimate(
        provider="groq",
        model="model-not-in-table",
        prompt_tokens=100,
        completion_tokens=100,
    )
    assert estimate.pricing_known is False
    assert estimate.estimated_usd is None


def test_zero_tokens_yields_zero_cost(engine: CostEngine) -> None:
    estimate = engine.estimate(
        provider="groq",
        model="llama-3.1-8b-instant",
        prompt_tokens=0,
        completion_tokens=0,
    )
    assert estimate.estimated_usd == 0.0
    assert estimate.pricing_known is True


def test_empty_pricing_table_returns_unknown() -> None:
    engine = CostEngine(pricing={})
    estimate = engine.estimate("anything", "anything", 100, 100)
    assert estimate.pricing_known is False
