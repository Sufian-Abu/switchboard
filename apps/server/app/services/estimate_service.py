"""Pre-flight cost estimation orchestration.

Given a prompt (and optionally a candidate provider/model list), returns the
estimated USD cost band per candidate based on the loaded pricing table and a
rule-of-thumb token estimate. No upstream calls are made.
"""
from __future__ import annotations

from app.api.deps import get_config, get_cost_engine
from app.schemas.chat import (
    ChatEstimateRequest,
    ChatEstimateResponse,
    ModelEstimate,
)
from router.estimation import estimate_messages_tokens


def _candidates_from_config() -> list[tuple[str, str]]:
    """Build the candidate list from every (provider, model) appearing in routing rules + default."""
    config = get_config()
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str]] = []

    def _add(provider: str | None, model: str | None) -> None:
        if not provider or not model:
            return
        key = (provider, model)
        if key in seen:
            return
        seen.add(key)
        candidates.append(key)

    default = config.get("default", {})
    _add(default.get("provider"), default.get("model"))

    for rule in config.get("routing", {}).get("rules", []) or []:
        use = rule.get("use") or {}
        _add(use.get("provider"), use.get("model"))
        for fb in rule.get("fallbacks") or []:
            _add(fb.get("provider"), fb.get("model"))

    return candidates


def estimate_request(request: ChatEstimateRequest) -> ChatEstimateResponse:
    cost_engine = get_cost_engine()

    if request.candidates is not None:
        candidates = [(c["provider"], c["model"]) for c in request.candidates]
    else:
        candidates = _candidates_from_config()

    messages_as_dict = [m.model_dump() for m in request.messages]
    input_tokens = estimate_messages_tokens(messages_as_dict)

    estimates: list[ModelEstimate] = []
    for provider, model in candidates:
        # Lower bound: input cost only (model returns 0 completion tokens).
        lower = cost_engine.estimate(provider, model, input_tokens, 0)
        # Upper bound: input + full max_completion_tokens.
        upper = cost_engine.estimate(
            provider, model, input_tokens, request.assumed_max_tokens
        )
        estimates.append(
            ModelEstimate(
                provider=provider,
                model=model,
                input_tokens_estimated=input_tokens,
                assumed_max_completion_tokens=request.assumed_max_tokens,
                estimated_usd_min=lower.estimated_usd,
                estimated_usd_max=upper.estimated_usd,
                input_rate_per_million=upper.input_rate_per_million,
                output_rate_per_million=upper.output_rate_per_million,
                pricing_known=upper.pricing_known,
            )
        )

    priced = [e for e in estimates if e.pricing_known and e.estimated_usd_max is not None]
    cheapest = min(priced, key=lambda e: e.estimated_usd_max) if priced else None
    most_expensive = max(priced, key=lambda e: e.estimated_usd_max) if priced else None

    return ChatEstimateResponse(
        input_tokens_estimated=input_tokens,
        assumed_max_completion_tokens=request.assumed_max_tokens,
        estimates=estimates,
        cheapest=cheapest,
        most_expensive=most_expensive,
    )
