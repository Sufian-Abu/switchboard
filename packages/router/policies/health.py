"""HealthPolicy — drops candidates that are degraded or unhealthy.

Consults the optional `ProviderHealthTracker` to decide which providers are
currently in a band the rule wants to avoid. When no tracker is configured,
the policy is a no-op (returns all candidates unchanged).
"""
from __future__ import annotations

from router.health import HealthBand, ProviderHealthTracker


_VALID_THRESHOLDS: tuple[HealthBand, ...] = ("degraded", "unhealthy")


class HealthPolicy:
    """Filter candidates whose rolling health is at-or-worse than the threshold."""

    def __init__(self, tracker: ProviderHealthTracker | None) -> None:
        self.tracker = tracker

    def threshold_from_rule(self, rule: dict) -> HealthBand | None:
        raw = rule.get("avoid_if_health")
        if raw is None:
            return None
        if raw not in _VALID_THRESHOLDS:
            raise ValueError(
                f"Rule {rule.get('name', '?')!r} has invalid `avoid_if_health`: "
                f"{raw!r} (must be one of {_VALID_THRESHOLDS})"
            )
        return raw

    def filter(
        self,
        candidates: list[tuple[str, str]],
        threshold: HealthBand | None,
    ) -> list[tuple[str, str]]:
        if threshold is None or self.tracker is None:
            return candidates
        kept: list[tuple[str, str]] = []
        for provider, model in candidates:
            snap = self.tracker.snapshot(provider)
            if not snap.is_avoidable(threshold):
                kept.append((provider, model))
        return kept
