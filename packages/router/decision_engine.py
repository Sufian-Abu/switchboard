"""Decision engine.

Translates a `ClassifiedTask` into a concrete `RoutingDecision` (provider +
model + fallback chain) by consulting the YAML routing config.

Resolution order:
  1. If the client explicitly passed `model`, short-circuit to `provider="manual"`.
  2. Otherwise, scan `routing.rules[*]` and return the first whose `when.task_type` matches.
     The rule's `use` + `fallbacks` form a candidate chain; if the rule sets a
     preference (e.g. `prefer: cheapest`) or `max_cost_per_call`, the
     `preference` module reorders/filters the chain before returning.
  3. If a matched rule is fully filtered out by `max_cost_per_call`, the engine
     continues scanning subsequent rules.
  4. Otherwise, fall back to `default.{provider,model}` in the config.
"""
from __future__ import annotations

from router.costing import CostEngine
from router.preference import PreferenceSpec, select_chain
from router.schemas import ClassifiedTask, RoutingDecision


class DecisionEngine:
    """Reads YAML routing rules and picks a provider+model per request."""

    def __init__(self, config: dict, cost_engine: CostEngine | None = None) -> None:
        self.config = config
        # `cost_engine` is only required when rules use `prefer: cheapest` or
        # `max_cost_per_call`. For backwards compat with tests that build a
        # DecisionEngine directly, fall back to an empty pricing table.
        self.cost_engine = cost_engine or CostEngine(pricing={})

    def decide(
        self,
        classified_task: ClassifiedTask,
        request_model: str | None = None,
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
            when = rule.get("when", {})
            if when.get("task_type") != classified_task.task_type:
                continue

            use = rule.get("use") or {}
            fallbacks_raw = rule.get("fallbacks") or []
            candidates: list[tuple[str, str]] = [(use["provider"], use["model"])]
            candidates.extend((fb["provider"], fb["model"]) for fb in fallbacks_raw)

            spec = PreferenceSpec.from_rule(rule)
            chain = select_chain(candidates, spec, self.cost_engine)
            if not chain:
                # Every candidate exceeded `max_cost_per_call` — try next rule.
                continue

            primary_provider, primary_model = chain[0]
            fallbacks = chain[1:]
            base_reason = f"Matched routing rule: {rule.get('name', 'unnamed_rule')}"
            if spec.policy == "cheapest":
                base_reason += " | reordered by cheapest-first preference"
            if spec.max_cost_per_call is not None:
                base_reason += f" | filtered by max_cost_per_call=${spec.max_cost_per_call}"

            return RoutingDecision(
                provider=primary_provider,
                model=primary_model,
                reason=base_reason,
                task_type=classified_task.task_type,
                fallbacks=fallbacks,
                cache_enabled=bool(rule.get("cache", False)),
            )

        default_provider = self.config.get("default", {}).get("provider", "mock")
        default_model = self.config.get("default", {}).get("model", "mock-default-model")
        return RoutingDecision(
            provider=default_provider,
            model=default_model,
            reason="No matching rule found. Using default route.",
            task_type=classified_task.task_type,
        )
