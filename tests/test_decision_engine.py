"""Unit tests for the DecisionEngine."""
from __future__ import annotations

from router.decision_engine import DecisionEngine
from router.schemas import ClassifiedTask


def _task(task_type: str) -> ClassifiedTask:
    return ClassifiedTask(task_type=task_type, reason="test")


def test_matches_rule_by_task_type(sample_config: dict) -> None:
    engine = DecisionEngine(config=sample_config)
    decision = engine.decide(_task("rewrite"))
    assert decision.provider == "mock"
    assert decision.model == "mock-rewrite-model"
    assert "rewrite_to_mock" in decision.reason
    assert decision.task_type == "rewrite"


def test_falls_through_to_default(sample_config: dict) -> None:
    engine = DecisionEngine(config=sample_config)
    decision = engine.decide(_task("general_chat"))
    assert decision.provider == "mock"
    assert decision.model == "mock-default-model"
    assert "default" in decision.reason.lower()


def test_explicit_model_short_circuits(sample_config: dict) -> None:
    engine = DecisionEngine(config=sample_config)
    decision = engine.decide(_task("rewrite"), request_model="gpt-4o")
    # Client-supplied model wins regardless of matching rules.
    assert decision.provider == "manual"
    assert decision.model == "gpt-4o"


def test_empty_config_uses_built_in_defaults() -> None:
    engine = DecisionEngine(config={})
    decision = engine.decide(_task("anything"))
    assert decision.provider == "mock"
    assert decision.model == "mock-default-model"


def test_unnamed_rule_still_matches() -> None:
    engine = DecisionEngine(
        config={
            "routing": {
                "rules": [
                    {
                        "when": {"task_type": "summarization"},
                        "use": {"provider": "mock", "model": "m-sum"},
                    }
                ]
            }
        }
    )
    decision = engine.decide(_task("summarization"))
    assert decision.model == "m-sum"
    assert "unnamed_rule" in decision.reason
