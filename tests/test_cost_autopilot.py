"""Tests for AI Cost Autopilot: `immune_to_soft_cap: true` on rules.

The basic soft cap reorders every fallback chain cheapest-first when
today's spend crosses DAILY_SOFT_CAP_PCT * MAX_DAILY_USD. The autopilot
flag lets you keep critical traffic (e.g. work Slack, production logs)
on its premium model even while non-critical traffic degrades.
"""
from __future__ import annotations

from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.policies import BudgetPolicy
from router.preference import PreferenceSpec
from router.schemas import ClassifiedTask


# --- BudgetPolicy unit -----------------------------------------------------


def test_budget_policy_no_change_when_soft_cap_off() -> None:
    policy = BudgetPolicy()
    rule = {"name": "demo"}
    spec, override = policy.adjust(rule, PreferenceSpec(), soft_cap_active=False, candidates_count=3)
    assert override is False
    assert spec.policy == "manual"  # unchanged


def test_budget_policy_no_change_when_single_candidate() -> None:
    policy = BudgetPolicy()
    rule = {"name": "demo"}
    spec, override = policy.adjust(rule, PreferenceSpec(), soft_cap_active=True, candidates_count=1)
    assert override is False  # nothing to reorder


def test_budget_policy_forces_cheapest_under_soft_cap() -> None:
    policy = BudgetPolicy()
    rule = {"name": "demo"}
    spec, override = policy.adjust(rule, PreferenceSpec(), soft_cap_active=True, candidates_count=3)
    assert override is True
    assert spec.policy == "cheapest"


def test_budget_policy_respects_immune_flag() -> None:
    """Rules marked `immune_to_soft_cap: true` keep their normal preference."""
    policy = BudgetPolicy()
    rule = {"name": "critical_path", "immune_to_soft_cap": True}
    spec, override = policy.adjust(rule, PreferenceSpec(), soft_cap_active=True, candidates_count=3)
    assert override is False
    assert spec.policy == "manual"  # unchanged


# --- End-to-end via DecisionEngine ----------------------------------------


def _two_candidate_rule(name: str, immune: bool = False) -> dict:
    rule = {
        "name": name,
        "when": {"task_type": "reasoning"},
        "use":       {"provider": "cloud", "model": "expensive"},
        "fallbacks": [{"provider": "cloud", "model": "cheap"}],
    }
    if immune:
        rule["immune_to_soft_cap"] = True
    return rule


def _engine_with_rule(rule: dict) -> DecisionEngine:
    pricing = {
        "cloud": {
            "expensive": {"input": 10.0, "output": 30.0},
            "cheap":     {"input": 0.10, "output": 0.30},
        }
    }
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {"rules": [rule]},
    }
    return DecisionEngine(config=config, cost_engine=CostEngine(pricing=pricing))


def test_engine_degrades_normal_rule_under_soft_cap() -> None:
    engine = _engine_with_rule(_two_candidate_rule("normal"))
    task = ClassifiedTask(task_type="reasoning", reason="")

    # No soft cap → original primary serves.
    assert engine.decide(task, budget_soft_cap=False).model == "expensive"
    # Soft cap engaged → cheapest wins.
    decision = engine.decide(task, budget_soft_cap=True)
    assert decision.model == "cheap"
    assert "soft-cap engaged" in decision.reason


def test_engine_keeps_immune_rule_premium_under_soft_cap() -> None:
    engine = _engine_with_rule(_two_candidate_rule("critical_work_slack", immune=True))
    task = ClassifiedTask(task_type="reasoning", reason="")

    # Both with and without soft cap engaged → keeps the premium primary.
    assert engine.decide(task, budget_soft_cap=False).model == "expensive"
    decision = engine.decide(task, budget_soft_cap=True)
    assert decision.model == "expensive"
    assert "soft-cap engaged" not in decision.reason


def test_mixed_rules_only_non_immune_ones_degrade() -> None:
    """In one config: critical rule stays premium, normal rule degrades."""
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
                    "name": "casual_chat",
                    "when": {"task_type": "general_chat"},
                    "use":       {"provider": "cloud", "model": "expensive"},
                    "fallbacks": [{"provider": "cloud", "model": "cheap"}],
                },
                {
                    "name": "work_critical",
                    "when": {"task_type": "reasoning"},
                    "use":       {"provider": "cloud", "model": "expensive"},
                    "fallbacks": [{"provider": "cloud", "model": "cheap"}],
                    "immune_to_soft_cap": True,
                },
            ]
        },
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing=pricing))

    # Soft cap engaged.
    casual = engine.decide(ClassifiedTask(task_type="general_chat", reason=""), budget_soft_cap=True)
    critical = engine.decide(ClassifiedTask(task_type="reasoning", reason=""), budget_soft_cap=True)

    assert casual.model == "cheap"        # degraded
    assert critical.model == "expensive"  # kept premium thanks to immune flag
