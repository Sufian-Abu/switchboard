"""Internal dataclasses passed between router components.

These are *not* the HTTP-facing Pydantic models — those live under
`apps/server/app/schemas/`. These types are deliberately tiny and free of any
web framework dependency so the router package can be reused outside FastAPI.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClassifiedTask:
    """Output of the classifier — what kind of task the user appears to want."""

    task_type: str
    reason: str


@dataclass
class RoutingDecision:
    """Output of the decision engine — which provider+model should serve the task.

    `fallbacks` is the ordered list of `(provider, model)` pairs to try if the
    primary fails with a retryable error. Empty list means no fallback.
    `cache_enabled` is True when the matched rule sets `cache: true`.
    `cohort` is the A/B-test cohort label when the rule used `split:`, else None.
    """

    provider: str
    model: str
    reason: str
    task_type: str
    fallbacks: list[tuple[str, str]] = field(default_factory=list)
    cache_enabled: bool = False
    cohort: str | None = None


@dataclass
class ProviderResponse:
    """Normalized response from any provider, ready for the API envelope."""

    content: str
    prompt_tokens: int
    completion_tokens: int
