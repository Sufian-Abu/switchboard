"""Cost-aware candidate selection used by DecisionEngine.

Given a primary + fallback chain and a `prefer:` policy, this module reorders
candidates so the preferred one becomes the primary attempt. The original
chain order is preserved as the tie-breaker, so existing configs are stable.

Supported policies:
    - `manual` (default) — no reordering; primary stays primary.
    - `cheapest` — reorder by expected cost (lowest first) for an assumed
      worst-case completion. Uses the CostEngine + the rule's `assume_max_tokens`
      (default 256) to score each candidate.

Additionally, `max_cost_per_call` filters out any candidate whose worst-case
estimated cost exceeds the cap. If every candidate fails the filter, the rule
falls through to the next routing rule (or default) — same as if no rule
matched.
"""
from __future__ import annotations

from dataclasses import dataclass

from router.costing import CostEngine


@dataclass
class PreferenceSpec:
    """Parsed preference config for a routing rule."""

    policy: str = "manual"  # "manual" | "cheapest"
    max_cost_per_call: float | None = None
    assume_max_tokens: int = 256
    assume_prompt_tokens: int = 64  # rough average; only used for scoring
    # When `max_cost_per_call` is set, candidates without a price entry
    # are excluded by default — we can't prove they're under the cap.
    # Set `allow_unknown_pricing_under_cap: true` on the rule to opt back
    # into the old permissive behaviour (use only for trusted local models
    # like Ollama where you know the price is effectively zero).
    allow_unknown_pricing_under_cap: bool = False

    @classmethod
    def from_rule(cls, rule: dict) -> "PreferenceSpec":
        return cls(
            policy=str(rule.get("prefer", "manual")).lower(),
            max_cost_per_call=rule.get("max_cost_per_call"),
            assume_max_tokens=int(rule.get("assume_max_tokens", 256)),
            assume_prompt_tokens=int(rule.get("assume_prompt_tokens", 64)),
            allow_unknown_pricing_under_cap=bool(
                rule.get("allow_unknown_pricing_under_cap", False)
            ),
        )


def select_chain(
    candidates: list[tuple[str, str]],
    spec: PreferenceSpec,
    cost_engine: CostEngine,
) -> list[tuple[str, str]]:
    """Apply the preference policy + cost cap to the (provider, model) candidate list.

    Returns a possibly-reordered, possibly-filtered list. Empty list means
    every candidate was excluded by `max_cost_per_call`.
    """
    if not candidates:
        return []

    # Score each: (cost_for_max_completion, original_index, provider, model).
    scored: list[tuple[float | None, int, str, str]] = []
    for i, (provider, model) in enumerate(candidates):
        estimate = cost_engine.estimate(
            provider, model, spec.assume_prompt_tokens, spec.assume_max_tokens
        )
        cost = estimate.estimated_usd if estimate.pricing_known else None
        scored.append((cost, i, provider, model))

    # Filter by max_cost_per_call. SECURITY: when a cap is set, candidates with
    # no pricing entry are excluded by default — we can't prove they're under
    # the cap, so we fail closed. Set `allow_unknown_pricing_under_cap: true`
    # on the rule to opt back into the legacy permissive behaviour (only safe
    # for providers you trust are free, e.g. local Ollama).
    if spec.max_cost_per_call is not None:
        cap = float(spec.max_cost_per_call)
        if spec.allow_unknown_pricing_under_cap:
            scored = [s for s in scored if s[0] is None or s[0] <= cap]
        else:
            scored = [s for s in scored if s[0] is not None and s[0] <= cap]

    if not scored:
        return []

    if spec.policy == "cheapest":
        # Unknown-pricing candidates go to the end (we can't prove they're cheap).
        scored.sort(key=lambda s: (s[0] is None, s[0] if s[0] is not None else float("inf"), s[1]))
    # else: policy=='manual' → preserve original order (already by `i`).

    return [(s[2], s[3]) for s in scored]
