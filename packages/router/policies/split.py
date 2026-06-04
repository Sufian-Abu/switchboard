"""SplitPolicy — A/B traffic-splitting cohort selection.

When a rule has a `split:` block, this policy parses the cohort definitions
and picks one. The pick is **sticky** when a `sticky_key` is provided (the
same key always picks the same cohort given the same cohort definitions) —
so a returning user doesn't bounce between models. Falls back to weighted
random when no sticky key is available.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class CohortChoice:
    """The result of picking from a `split:` block."""

    name: str
    provider: str
    model: str
    sticky: bool  # True if the pick was deterministic via sticky_key


@dataclass(frozen=True)
class _CohortSpec:
    name: str
    provider: str
    model: str
    weight: float


class SplitPolicy:
    """Pick one cohort from a rule's `split:` block, sticky when possible."""

    def parse(self, rule: dict[str, Any]) -> list[_CohortSpec] | None:
        """Validate and parse a rule's `split:` block, or return None if absent."""
        split = rule.get("split")
        if split is None:
            return None

        rule_name = rule.get("name", "unnamed_rule")
        if not isinstance(split, list):
            raise ValueError(
                f"Rule {rule_name!r} `split:` must be a list of cohort entries, "
                f"got {type(split).__name__}."
            )
        if not split:
            raise ValueError(f"Rule {rule_name!r} `split:` is empty.")

        cohorts: list[_CohortSpec] = []
        for i, entry in enumerate(split):
            if not isinstance(entry, dict):
                raise ValueError(f"Rule {rule_name!r} split[{i}] must be a mapping.")
            name = entry.get("name") or f"cohort_{i}"
            provider = entry.get("provider")
            model = entry.get("model")
            weight = entry.get("weight", 1.0)
            if not provider or not model:
                raise ValueError(
                    f"Rule {rule_name!r} split[{i}] must have `provider` and `model`."
                )
            try:
                weight_f = float(weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Rule {rule_name!r} split[{i}] `weight` must be a number, got {weight!r}."
                ) from exc
            if weight_f <= 0:
                raise ValueError(
                    f"Rule {rule_name!r} split[{i}] `weight` must be > 0, got {weight_f}."
                )
            cohorts.append(_CohortSpec(name, provider, model, weight_f))
        return cohorts

    def pick(
        self,
        rule: dict[str, Any],
        sticky_key: str | None = None,
    ) -> CohortChoice | None:
        """Pick a cohort. Returns None when the rule doesn't use split."""
        cohorts = self.parse(rule)
        if cohorts is None:
            return None
        if sticky_key:
            chosen = self._sticky_pick(cohorts, sticky_key)
            return CohortChoice(
                name=chosen.name,
                provider=chosen.provider,
                model=chosen.model,
                sticky=True,
            )
        chosen = self._random_pick(cohorts)
        return CohortChoice(
            name=chosen.name,
            provider=chosen.provider,
            model=chosen.model,
            sticky=False,
        )

    def _sticky_pick(self, cohorts: list[_CohortSpec], sticky_key: str) -> _CohortSpec:
        """Deterministic weighted pick: hash sticky_key into the weight ranges."""
        # We hash both the key and the cohort identities so that adding/removing
        # cohorts cleanly rebalances assignment instead of preserving stale picks.
        ident = "|".join(f"{c.name}:{c.weight}" for c in cohorts)
        digest = hashlib.sha256(f"{sticky_key}|{ident}".encode("utf-8")).digest()
        # Take 8 bytes → uint64; modulo the total weight (scaled to integers).
        # Multiplying by 1000 lets us support fractional weights cleanly.
        total_scaled = int(sum(c.weight for c in cohorts) * 1000)
        if total_scaled <= 0:  # defensive — should never happen, weights validated > 0
            total_scaled = 1
        target = int.from_bytes(digest[:8], "big") % total_scaled
        cumulative = 0
        for c in cohorts:
            cumulative += int(c.weight * 1000)
            if target < cumulative:
                return c
        return cohorts[-1]  # numerical fallback

    def _random_pick(self, cohorts: list[_CohortSpec]) -> _CohortSpec:
        return random.choices(
            cohorts, weights=[c.weight for c in cohorts], k=1
        )[0]
