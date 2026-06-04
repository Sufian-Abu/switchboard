"""Tests for cost-aware preference reordering and max-cost filtering."""
from __future__ import annotations

from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.preference import PreferenceSpec, select_chain
from router.schemas import ClassifiedTask


def _engine_with_pricing(pricing: dict) -> DecisionEngine:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {"rules": []},
    }
    return DecisionEngine(config=config, cost_engine=CostEngine(pricing=pricing))


def test_select_chain_manual_preserves_order() -> None:
    candidates = [("a", "m1"), ("b", "m2"), ("c", "m3")]
    engine = CostEngine(pricing={})
    chain = select_chain(candidates, PreferenceSpec(policy="manual"), engine)
    assert chain == candidates


def test_select_chain_cheapest_reorders() -> None:
    candidates = [
        ("cloud", "expensive"),
        ("cloud", "cheap"),
        ("cloud", "mid"),
    ]
    engine = CostEngine(
        pricing={
            "cloud": {
                "expensive": {"input": 10.0, "output": 30.0},
                "cheap": {"input": 0.10, "output": 0.30},
                "mid": {"input": 1.00, "output": 3.00},
            }
        }
    )
    chain = select_chain(candidates, PreferenceSpec(policy="cheapest"), engine)
    assert chain == [("cloud", "cheap"), ("cloud", "mid"), ("cloud", "expensive")]


def test_select_chain_unknown_pricing_sorts_last_under_cheapest() -> None:
    candidates = [("unknown", "x"), ("cloud", "y")]
    engine = CostEngine(pricing={"cloud": {"y": {"input": 1.0, "output": 1.0}}})
    chain = select_chain(candidates, PreferenceSpec(policy="cheapest"), engine)
    # cloud/y has known pricing → goes first; unknown/x trails.
    assert chain[0] == ("cloud", "y")
    assert chain[1] == ("unknown", "x")


def test_max_cost_per_call_filters_expensive() -> None:
    candidates = [("cloud", "big"), ("cloud", "small")]
    engine = CostEngine(
        pricing={
            "cloud": {
                "big": {"input": 100.0, "output": 100.0},
                "small": {"input": 0.05, "output": 0.05},
            }
        }
    )
    spec = PreferenceSpec(
        policy="manual",
        max_cost_per_call=0.001,
        assume_max_tokens=256,
        assume_prompt_tokens=64,
    )
    chain = select_chain(candidates, spec, engine)
    # cloud/big = 100 USD/M * (64+256) ÷ 1M = $0.032 → exceeds cap.
    # cloud/small = 0.05 * 320 / 1M = $0.000016 → under cap.
    assert chain == [("cloud", "small")]


def test_max_cost_per_call_filters_all_returns_empty() -> None:
    candidates = [("cloud", "expensive")]
    engine = CostEngine(pricing={"cloud": {"expensive": {"input": 100.0, "output": 100.0}}})
    spec = PreferenceSpec(policy="manual", max_cost_per_call=0.000001)
    assert select_chain(candidates, spec, engine) == []


def test_engine_falls_through_when_max_cost_filters_all() -> None:
    """Rule whose only candidate exceeds max_cost_per_call → engine moves to next rule."""
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "too_expensive",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "cloud", "model": "expensive"},
                    "max_cost_per_call": 0.000001,
                },
                {
                    "name": "fallback_rule",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "mock", "model": "mock-rewrite-model"},
                },
            ]
        },
    }
    engine = DecisionEngine(
        config=config,
        cost_engine=CostEngine(
            pricing={"cloud": {"expensive": {"input": 100.0, "output": 100.0}}}
        ),
    )
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))
    assert decision.provider == "mock"
    assert decision.model == "mock-rewrite-model"


def test_engine_cheapest_reorders_chain() -> None:
    """`prefer: cheapest` should make a cheaper fallback the primary."""
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "cheapest_demo",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "cloud", "model": "expensive"},
                    "fallbacks": [{"provider": "cloud", "model": "cheap"}],
                    "prefer": "cheapest",
                }
            ]
        },
    }
    engine = DecisionEngine(
        config=config,
        cost_engine=CostEngine(
            pricing={
                "cloud": {
                    "expensive": {"input": 10.0, "output": 30.0},
                    "cheap": {"input": 0.10, "output": 0.30},
                }
            }
        ),
    )
    decision = engine.decide(ClassifiedTask(task_type="rewrite", reason=""))
    assert decision.provider == "cloud"
    assert decision.model == "cheap"
    assert decision.fallbacks == [("cloud", "expensive")]
    assert "cheapest-first" in decision.reason
