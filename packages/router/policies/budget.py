"""BudgetPolicy — adjusts the preference spec when the daily soft-cap is engaged.

Only does work when the orchestration layer has detected the soft cap as
active (e.g. today's spend ≥ MAX_DAILY_USD * DAILY_SOFT_CAP_PCT). Rules
marked `immune_to_soft_cap: true` are exempt — useful for critical
channels you don't want to degrade automatically.
"""
from __future__ import annotations

from router.preference import PreferenceSpec


class BudgetPolicy:
    """Override the preference spec when the daily soft-cap fires."""

    def adjust(
        self,
        rule: dict,
        spec: PreferenceSpec,
        soft_cap_active: bool,
        candidates_count: int,
    ) -> tuple[PreferenceSpec, bool]:
        """Returns (adjusted_spec, did_override).

        did_override drives the routing-reason text on the orchestrator side.
        """
        if not soft_cap_active:
            return spec, False
        if candidates_count <= 1:
            # Nothing to reorder — leave the spec alone.
            return spec, False
        if rule.get("immune_to_soft_cap"):
            # AI Cost Autopilot: rule opted out of automatic degradation.
            return spec, False
        # Force cheapest-first across the chain. Preserve the rest of the
        # original spec so `max_cost_per_call` and unknown-pricing semantics
        # still apply.
        return (
            PreferenceSpec(
                policy="cheapest",
                max_cost_per_call=spec.max_cost_per_call,
                assume_max_tokens=spec.assume_max_tokens,
                assume_prompt_tokens=spec.assume_prompt_tokens,
                allow_unknown_pricing_under_cap=spec.allow_unknown_pricing_under_cap,
            ),
            True,
        )
