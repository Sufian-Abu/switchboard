"""CostPolicy — applies the preference spec to a candidate chain.

Thin wrapper around `select_chain` so the engine doesn't import preference
internals directly and can be tested with a fake cost engine if needed.
"""
from __future__ import annotations

from router.costing import CostEngine
from router.preference import PreferenceSpec, select_chain


class CostPolicy:
    """Reorders / filters candidates by cost preference."""

    def __init__(self, cost_engine: CostEngine) -> None:
        self.cost_engine = cost_engine

    def select(
        self,
        candidates: list[tuple[str, str]],
        spec: PreferenceSpec,
    ) -> list[tuple[str, str]]:
        return select_chain(candidates, spec, self.cost_engine)
