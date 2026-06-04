"""Eval harness — score routing decisions against a YAML test suite.

Each case in the YAML supplies a `messages` list and an `expect` block. The
harness runs the classifier + decision engine (no upstream calls, no tokens
billed) and checks the result. Supported assertions:

    expect:
      task_type: <str>                       # exact match required
      provider: <str>                        # exact match required
      model: <str>                           # exact match required
      provider_in: [<str>, ...]              # any of these
      model_contains: <substring>            # substring match

Run with:

    python -m router.eval --cases evals/example.yaml
    python -m router.eval --cases evals/example.yaml --config configs/config.yaml

Exits 0 if all cases pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from router.classifier import TaskClassifier
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.schemas import ClassifiedTask, RoutingDecision


@dataclass
class CaseResult:
    name: str
    passed: bool
    details: list[str]
    classified: ClassifiedTask
    decision: RoutingDecision


def _check_expectations(expect: dict, classified: ClassifiedTask, decision: RoutingDecision) -> list[str]:
    """Return a list of failure messages (empty list = all pass)."""
    failures: list[str] = []

    if "task_type" in expect and classified.task_type != expect["task_type"]:
        failures.append(
            f"task_type: expected {expect['task_type']!r}, got {classified.task_type!r}"
        )

    if "provider" in expect and decision.provider != expect["provider"]:
        failures.append(
            f"provider: expected {expect['provider']!r}, got {decision.provider!r}"
        )

    if "provider_in" in expect and decision.provider not in expect["provider_in"]:
        failures.append(
            f"provider_in: expected one of {expect['provider_in']!r}, got {decision.provider!r}"
        )

    if "model" in expect and decision.model != expect["model"]:
        failures.append(
            f"model: expected {expect['model']!r}, got {decision.model!r}"
        )

    if "model_contains" in expect and expect["model_contains"] not in decision.model:
        failures.append(
            f"model_contains: expected substring {expect['model_contains']!r}, got {decision.model!r}"
        )

    return failures


def run_suite(cases_path: Path, config_path: Path, pricing_path: Path | None = None) -> list[CaseResult]:
    """Run every case in the YAML against a freshly-built classifier + engine."""
    with cases_path.open(encoding="utf-8") as fp:
        suite = yaml.safe_load(fp) or {}
    with config_path.open(encoding="utf-8") as fp:
        config = yaml.safe_load(fp) or {}
    pricing: dict = {}
    if pricing_path and pricing_path.exists():
        with pricing_path.open(encoding="utf-8") as fp:
            pricing = yaml.safe_load(fp) or {}

    classifier = TaskClassifier()  # keyword-only — keep eval offline
    cost_engine = CostEngine(pricing=pricing)
    engine = DecisionEngine(config=config, cost_engine=cost_engine)

    results: list[CaseResult] = []
    for case in suite.get("cases", []):
        name = case.get("name", "<unnamed>")
        messages = case.get("messages") or []
        expect = case.get("expect") or {}
        classified = classifier.classify(messages)
        decision = engine.decide(classified)
        failures = _check_expectations(expect, classified, decision)
        results.append(
            CaseResult(
                name=name,
                passed=not failures,
                details=failures,
                classified=classified,
                decision=decision,
            )
        )
    return results


def _print_report(results: list[CaseResult]) -> int:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        print(f"[{marker}] {r.name}")
        print(
            f"       classified={r.classified.task_type!r:30s} "
            f"-> {r.decision.provider}/{r.decision.model}"
        )
        if not r.passed:
            for detail in r.details:
                print(f"       - {detail}")
    print(f"\n{passed}/{len(results)} passed; {failed} failed")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="router.eval", description=__doc__.splitlines()[0])
    parser.add_argument("--cases", required=True, type=Path, help="Path to eval YAML")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/config.yaml"),
        help="Routing config (default: configs/config.yaml)",
    )
    parser.add_argument(
        "--pricing",
        type=Path,
        default=Path("configs/pricing.yaml"),
        help="Pricing table (default: configs/pricing.yaml)",
    )
    args = parser.parse_args(argv)
    results = run_suite(args.cases, args.config, args.pricing)
    return _print_report(results)


if __name__ == "__main__":
    sys.exit(main())
