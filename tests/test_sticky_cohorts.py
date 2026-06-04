"""Tests for sticky A/B cohort assignment.

Random per-request picks contaminate A/B comparisons because a returning
user bounces between cohorts. Sticky assignment hashes a user/session
identifier to a deterministic cohort so the same user always sees the
same model variant.
"""
from __future__ import annotations

from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.policies import SplitPolicy
from router.schemas import ClassifiedTask


def _split_rule(weights: tuple[float, float] = (50, 50)) -> dict:
    return {
        "name": "rewrite_ab",
        "when": {"task_type": "rewrite"},
        "split": [
            {"name": "cohort_a", "provider": "groq", "model": "llama-3.1-8b-instant", "weight": weights[0]},
            {"name": "cohort_b", "provider": "gemini", "model": "gemini-2.5-flash", "weight": weights[1]},
        ],
    }


# --- SplitPolicy unit ------------------------------------------------------


def test_sticky_pick_is_deterministic_per_key() -> None:
    policy = SplitPolicy()
    rule = _split_rule()
    # Same key → same cohort, every time.
    name1 = policy.pick(rule, sticky_key="user-42").name
    name2 = policy.pick(rule, sticky_key="user-42").name
    name3 = policy.pick(rule, sticky_key="user-42").name
    assert name1 == name2 == name3
    # Reported as sticky.
    assert policy.pick(rule, sticky_key="user-42").sticky is True


def test_sticky_picks_differ_across_keys() -> None:
    """With many users at 50/50 we expect both cohorts to appear."""
    policy = SplitPolicy()
    rule = _split_rule()
    seen = {policy.pick(rule, sticky_key=f"user-{i}").name for i in range(50)}
    assert seen == {"cohort_a", "cohort_b"}


def test_sticky_respects_weights() -> None:
    """Heavily skewed weights should heavily skew sticky assignment too."""
    policy = SplitPolicy()
    rule = _split_rule(weights=(99, 1))
    chosen = [policy.pick(rule, sticky_key=f"user-{i}").name for i in range(200)]
    # At least 90% of distinct users hit the heavy cohort.
    assert chosen.count("cohort_a") > 180


def test_no_sticky_key_falls_back_to_random() -> None:
    policy = SplitPolicy()
    rule = _split_rule()
    result = policy.pick(rule, sticky_key=None)
    assert result is not None
    assert result.sticky is False
    assert result.name in {"cohort_a", "cohort_b"}


def test_sticky_pick_rebalances_when_cohorts_change() -> None:
    """Adding/removing a cohort should change at least some users' assignment.
    (Important: prevents stale assignments locking users to deleted cohorts.)"""
    policy = SplitPolicy()
    rule_two = _split_rule()
    rule_three = {
        "name": "rewrite_ab",
        "when": {"task_type": "rewrite"},
        "split": rule_two["split"] + [
            {"name": "cohort_c", "provider": "ollama", "model": "llama3.2:1b", "weight": 50}
        ],
    }
    diff = 0
    for i in range(100):
        key = f"user-{i}"
        if policy.pick(rule_two, sticky_key=key).name != policy.pick(rule_three, sticky_key=key).name:
            diff += 1
    # With one new cohort weighted equally, ~1/3 of users should land elsewhere.
    assert diff > 15


# --- End-to-end via DecisionEngine ----------------------------------------


def test_engine_uses_metadata_user_id_for_stickiness() -> None:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {"rules": [_split_rule()]},
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    task = ClassifiedTask(task_type="rewrite", reason="")

    metadata = {"user_id": "alice"}
    cohorts = [engine.decide(task, metadata=metadata).cohort for _ in range(10)]
    assert len(set(cohorts)) == 1  # all the same
    # And the reason text marks it sticky.
    decision = engine.decide(task, metadata=metadata)
    assert "(sticky)" in decision.reason


def test_engine_falls_back_to_session_id() -> None:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {"rules": [_split_rule()]},
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    task = ClassifiedTask(task_type="rewrite", reason="")

    metadata = {"session_id": "sess-xyz"}
    cohorts = [engine.decide(task, metadata=metadata).cohort for _ in range(5)]
    assert len(set(cohorts)) == 1
    assert "(sticky)" in engine.decide(task, metadata=metadata).reason


def test_engine_without_sticky_metadata_uses_random() -> None:
    config = {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {"rules": [_split_rule()]},
    }
    engine = DecisionEngine(config=config, cost_engine=CostEngine(pricing={}))
    task = ClassifiedTask(task_type="rewrite", reason="")
    decision = engine.decide(task)  # no metadata
    assert "(sticky)" not in decision.reason
    assert decision.cohort in {"cohort_a", "cohort_b"}
