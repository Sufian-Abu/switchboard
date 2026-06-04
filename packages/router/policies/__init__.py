"""Routing policies. Each module handles one concern; DecisionEngine composes them.

Pipeline order for a request:

    1. MatchingPolicy   — does this rule's `when:` match?
    2. SplitPolicy      — if `split:` is present, pick a cohort and return.
    3. HealthPolicy     — drop candidates that are degraded/unhealthy.
    4. BudgetPolicy     — under soft-cap, force cheapest-first (unless immune).
    5. CostPolicy       — apply `prefer:` + `max_cost_per_call:` + chain order.

Each policy is a small class with constructor-injected dependencies, easy to
unit-test in isolation, and easy to extend (add `RiskPolicy` later by
inserting it in the pipeline).
"""
from router.policies.budget import BudgetPolicy
from router.policies.cost import CostPolicy
from router.policies.health import HealthPolicy
from router.policies.matching import MatchingPolicy
from router.policies.split import CohortChoice, SplitPolicy

__all__ = [
    "BudgetPolicy",
    "CohortChoice",
    "CostPolicy",
    "HealthPolicy",
    "MatchingPolicy",
    "SplitPolicy",
]
