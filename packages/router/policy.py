"""Routing policy wrapper.

Placeholder for Phase 2: as policies grow (fallback chains, cost guards, tenant
overrides), they will live here behind a stable surface. Phase 1 only exposes a
rules getter; the DecisionEngine currently reads config directly.
"""
from __future__ import annotations

from typing import Any


class PolicyEngine:
    """Thin wrapper over the `routing.rules` section of the YAML config."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_rules(self) -> list[dict[str, Any]]:
        """Return the list of routing rules from the config (empty if missing)."""
        return self.config.get("routing", {}).get("rules", [])
