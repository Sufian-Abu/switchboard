"""Tests for the eval harness CLI."""
from __future__ import annotations

from pathlib import Path

from router.eval import _check_expectations, run_suite
from router.schemas import ClassifiedTask, RoutingDecision


def test_check_expectations_all_pass() -> None:
    failures = _check_expectations(
        expect={
            "task_type": "rewrite",
            "provider": "groq",
            "model_contains": "llama",
            "provider_in": ["groq", "mock"],
        },
        classified=ClassifiedTask(task_type="rewrite", reason=""),
        decision=RoutingDecision(
            provider="groq", model="llama-3.1-8b-instant", reason="", task_type="rewrite"
        ),
    )
    assert failures == []


def test_check_expectations_reports_each_mismatch() -> None:
    failures = _check_expectations(
        expect={
            "task_type": "rewrite",
            "provider": "groq",
            "model": "should-not-match",
            "provider_in": ["groq"],
            "model_contains": "nope",
        },
        classified=ClassifiedTask(task_type="summarization", reason=""),
        decision=RoutingDecision(
            provider="mock", model="other", reason="", task_type="summarization"
        ),
    )
    # task_type, provider, model, provider_in, model_contains → 5 distinct failures.
    assert len(failures) == 5


def test_run_suite_against_test_configs(tmp_path: Path) -> None:
    cases_yaml = tmp_path / "cases.yaml"
    cases_yaml.write_text(
        """
cases:
  - name: rewrite_routes_to_mock
    messages:
      - role: user
        content: please rewrite this email
    expect:
      task_type: rewrite
      provider: mock
"""
    )
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        """
default: {provider: mock, model: mock-default}
routing:
  rules:
    - name: rewrite_to_mock
      when: {task_type: rewrite}
      use: {provider: mock, model: mock-rewrite-model}
"""
    )
    results = run_suite(cases_yaml, config_yaml)
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].decision.provider == "mock"
    assert results[0].decision.model == "mock-rewrite-model"
