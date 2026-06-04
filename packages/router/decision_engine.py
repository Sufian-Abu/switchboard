"""Decision engine.

Translates a `ClassifiedTask` + optional request metadata into a concrete
`RoutingDecision` (provider + model + fallback chain) by consulting the YAML
routing config.

`when:` clauses support two key shapes today:

  - `task_type: <str>` — match the classified task type.
  - `metadata.<field>: <value>` — match a field in the request's `metadata`
    block. Used by upstream integrations (e.g. OpenClaw forwards
    `metadata.channel: whatsapp`) to do per-channel / per-tenant routing.

Multiple keys in the same `when:` are ANDed. A rule with no `when:` keys
matches everything (acts as a catch-all). A rule whose required
`metadata.foo` field isn't in the incoming request simply doesn't match,
and the engine continues scanning.

Resolution order:
  1. If the client explicitly passed `model`, short-circuit to `provider="manual"`.
  2. Otherwise, scan `routing.rules[*]` and return the first match.
     The rule's `use` + `fallbacks` form a candidate chain; if the rule sets
     a preference (`prefer: cheapest`) or `max_cost_per_call`, the chain is
     reordered/filtered before returning.
  3. If a matched rule is fully filtered out by `max_cost_per_call`, the
     engine continues scanning subsequent rules.
  4. Otherwise, fall back to `default.{provider,model}` in the config.
"""
from __future__ import annotations

from typing import Any

from router.costing import CostEngine
from router.health import HealthBand, ProviderHealthTracker
from router.preference import PreferenceSpec, select_chain
from router.schemas import ClassifiedTask, RoutingDecision


_METADATA_PREFIX = "metadata."


def _when_matches(
    when: dict, task_type: str, metadata: dict[str, Any] | None
) -> bool:
    """Return True iff every key/value in `when:` is satisfied by the request."""
    meta = metadata or {}
    for key, expected in when.items():
        if key == "task_type":
            if task_type != expected:
                return False
        elif isinstance(key, str) and key.startswith(_METADATA_PREFIX):
            field = key[len(_METADATA_PREFIX):]
            actual = meta.get(field)
            if actual != expected:
                return False
        else:
            # Unknown condition key — fail closed. Forces typos to be
            # noticed instead of silently matching everything.
            return False
    return True


class DecisionEngine:
    """Reads YAML routing rules and picks a provider+model per request."""

    _VALID_HEALTH_THRESHOLDS: tuple[HealthBand, ...] = ("degraded", "unhealthy")

    def __init__(
        self,
        config: dict,
        cost_engine: CostEngine | None = None,
        health_tracker: ProviderHealthTracker | None = None,
    ) -> None:
        self.config = config
        self.cost_engine = cost_engine or CostEngine(pricing={})
        # Optional. When None, `avoid_if_health:` rule clauses are no-ops.
        self.health_tracker = health_tracker

    def _filter_by_health(
        self,
        candidates: list[tuple[str, str]],
        threshold: HealthBand | None,
    ) -> list[tuple[str, str]]:
        """Drop candidates whose health is at or beyond the rule's threshold."""
        if threshold is None or self.health_tracker is None:
            return candidates
        kept: list[tuple[str, str]] = []
        for provider, model in candidates:
            snap = self.health_tracker.snapshot(provider)
            if not snap.is_avoidable(threshold):
                kept.append((provider, model))
        return kept

    def decide(
        self,
        classified_task: ClassifiedTask,
        request_model: str | None = None,
        metadata: dict[str, Any] | None = None,
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
            if not _when_matches(when, classified_task.task_type, metadata):
                continue

            use = rule.get("use") or {}
            fallbacks_raw = rule.get("fallbacks") or []
            candidates: list[tuple[str, str]] = [(use["provider"], use["model"])]
            candidates.extend((fb["provider"], fb["model"]) for fb in fallbacks_raw)

            # Health-based avoidance: drop providers whose rolling-window stats
            # cross the rule's threshold before any other filtering runs.
            health_threshold_raw = rule.get("avoid_if_health")
            health_threshold: HealthBand | None = None
            if health_threshold_raw is not None:
                if health_threshold_raw not in self._VALID_HEALTH_THRESHOLDS:
                    # Typo guard: raise rather than silently ignore.
                    raise ValueError(
                        f"Rule {rule.get('name', '?')!r} has invalid `avoid_if_health`: "
                        f"{health_threshold_raw!r} (must be one of {self._VALID_HEALTH_THRESHOLDS})"
                    )
                health_threshold = health_threshold_raw  # type: ignore[assignment]
            candidates = self._filter_by_health(candidates, health_threshold)
            if not candidates:
                # All candidates avoided due to health — fall through to next rule.
                continue

            spec = PreferenceSpec.from_rule(rule)
            chain = select_chain(candidates, spec, self.cost_engine)
            if not chain:
                # Every candidate exceeded `max_cost_per_call` — try next rule.
                continue

            primary_provider, primary_model = chain[0]
            fallbacks = chain[1:]
            base_reason = f"Matched routing rule: {rule.get('name', 'unnamed_rule')}"
            if health_threshold is not None:
                base_reason += f" | filtered by avoid_if_health={health_threshold}"
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
