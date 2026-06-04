"""Decision engine — a thin coordinator over the routing policies.

Translates a `ClassifiedTask` + request metadata into a concrete
`RoutingDecision` (provider + model + fallback chain) by running the
incoming request through a fixed pipeline of policies. Each policy lives in
`router.policies.*` and handles one concern; adding a new concern is a
matter of dropping in another policy step rather than growing this file.

Resolution order:
  1. Client-supplied `model:` short-circuit (provider="manual").
  2. For each rule, in YAML order:
       a. MatchingPolicy — does `when:` match this request?
       b. SplitPolicy    — if `split:` present, pick a cohort and return.
       c. HealthPolicy   — drop candidates whose health is at/below the rule's
                           `avoid_if_health:` threshold.
       d. BudgetPolicy   — under soft cap, force cheapest-first across the
                           chain unless the rule sets `immune_to_soft_cap: true`.
       e. CostPolicy     — apply `prefer:` / `max_cost_per_call:` to the
                           filtered chain.
       f. If the chain is non-empty, return a RoutingDecision.
  3. Default route (`default.{provider,model}` in the config).
"""
from __future__ import annotations

from typing import Any

from router.costing import CostEngine
from router.health import ProviderHealthTracker
from router.policies import (
    BudgetPolicy,
    CohortChoice,
    CostPolicy,
    HealthPolicy,
    MatchingPolicy,
    SplitPolicy,
)
from router.preference import PreferenceSpec
from router.schemas import ClassifiedTask, RoutingDecision


class DecisionEngine:
    """Routes a classified task to a provider/model + fallback chain."""

    def __init__(
        self,
        config: dict,
        cost_engine: CostEngine | None = None,
        health_tracker: ProviderHealthTracker | None = None,
    ) -> None:
        self.config = config
        cost_engine = cost_engine or CostEngine(pricing={})
        self.matching = MatchingPolicy()
        self.split = SplitPolicy()
        self.health = HealthPolicy(health_tracker)
        self.budget = BudgetPolicy()
        self.cost = CostPolicy(cost_engine)
        # Kept for backwards compatibility with tests that read it directly.
        self.cost_engine = cost_engine
        self.health_tracker = health_tracker

    def decide(
        self,
        classified_task: ClassifiedTask,
        request_model: str | None = None,
        metadata: dict[str, Any] | None = None,
        budget_soft_cap: bool = False,
    ) -> RoutingDecision:
        if request_model:
            return RoutingDecision(
                provider="manual",
                model=request_model,
                reason="Client explicitly requested a model.",
                task_type=classified_task.task_type,
            )

        rules = self.config.get("routing", {}).get("rules", [])
        for rule in rules:
            when = rule.get("when", {}) or {}
            if not self.matching.matches(when, classified_task.task_type, metadata):
                continue

            sticky_key = self._sticky_key_from_metadata(metadata)
            cohort: CohortChoice | None = self.split.pick(rule, sticky_key=sticky_key)
            if cohort is not None:
                return self._cohort_decision(rule, cohort, classified_task)

            decision = self._decide_for_rule(
                rule=rule,
                task_type=classified_task.task_type,
                budget_soft_cap=budget_soft_cap,
            )
            if decision is not None:
                return decision
            # Decision skipped (everything filtered out) — try the next rule.

        # Default route.
        default = self.config.get("default", {})
        return RoutingDecision(
            provider=default.get("provider", "mock"),
            model=default.get("model", "mock-default-model"),
            reason="No matching rule found. Using default route.",
            task_type=classified_task.task_type,
        )

    # --- private helpers ----------------------------------------------------

    def _decide_for_rule(
        self,
        rule: dict,
        task_type: str,
        budget_soft_cap: bool,
    ) -> RoutingDecision | None:
        use = rule.get("use") or {}
        fallbacks_raw = rule.get("fallbacks") or []
        candidates: list[tuple[str, str]] = [(use["provider"], use["model"])]
        candidates.extend((fb["provider"], fb["model"]) for fb in fallbacks_raw)

        health_threshold = self.health.threshold_from_rule(rule)
        candidates = self.health.filter(candidates, health_threshold)
        if not candidates:
            return None

        spec = PreferenceSpec.from_rule(rule)
        spec, budget_override = self.budget.adjust(
            rule, spec, budget_soft_cap, len(candidates)
        )
        chain = self.cost.select(candidates, spec)
        if not chain:
            return None

        primary_provider, primary_model = chain[0]
        fallbacks = chain[1:]
        return RoutingDecision(
            provider=primary_provider,
            model=primary_model,
            reason=self._build_reason(rule, spec, health_threshold, budget_override),
            task_type=task_type,
            fallbacks=fallbacks,
            cache_enabled=bool(rule.get("cache", False)),
        )

    def _cohort_decision(
        self,
        rule: dict,
        cohort: CohortChoice,
        classified_task: ClassifiedTask,
    ) -> RoutingDecision:
        reason = (
            f"Matched routing rule: {rule.get('name', 'unnamed_rule')} | "
            f"A/B cohort '{cohort.name}' selected"
        )
        if cohort.sticky:
            reason += " (sticky)"
        return RoutingDecision(
            provider=cohort.provider,
            model=cohort.model,
            reason=reason,
            task_type=classified_task.task_type,
            fallbacks=[],
            cache_enabled=bool(rule.get("cache", False)),
            cohort=cohort.name,
        )

    def _build_reason(
        self,
        rule: dict,
        spec: PreferenceSpec,
        health_threshold: str | None,
        budget_override: bool,
    ) -> str:
        reason = f"Matched routing rule: {rule.get('name', 'unnamed_rule')}"
        if health_threshold is not None:
            reason += f" | filtered by avoid_if_health={health_threshold}"
        if budget_override:
            reason += " | reordered cheapest-first (daily soft-cap engaged)"
        elif spec.policy == "cheapest":
            reason += " | reordered by cheapest-first preference"
        if spec.max_cost_per_call is not None:
            reason += f" | filtered by max_cost_per_call=${spec.max_cost_per_call}"
        return reason

    @staticmethod
    def _sticky_key_from_metadata(metadata: dict[str, Any] | None) -> str | None:
        """Pull a stable identifier for sticky A/B cohorts. Preferred → fallback."""
        if not metadata:
            return None
        for key in ("user_id", "user", "session_id", "session"):
            value = metadata.get(key)
            if value:
                return str(value)
        return None
