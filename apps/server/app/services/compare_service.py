"""Compare-all: fan out the same prompt across N (provider, model) candidates.

Used by `POST /v1/chat/compare` and the Playground UI's Compare-all button. All
candidates run concurrently via `asyncio.gather`. Per-candidate failures don't
abort the batch — the failure is recorded and the remaining results come back.
"""
from __future__ import annotations

import asyncio
import time

from app.api.deps import get_config, get_cost_engine, get_provider
from app.schemas.chat import (
    ChatCompareRequest,
    ChatCompareResponse,
    ComparisonResult,
)
from router.errors import ProviderError


def _candidates_from_config() -> list[tuple[str, str]]:
    """Same logic as the estimate service — gather every (provider, model) in the routing config."""
    config = get_config()
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str]] = []

    def _add(provider: str | None, model: str | None) -> None:
        if not provider or not model:
            return
        key = (provider, model)
        if key not in seen:
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


async def _run_one(
    provider_name: str,
    model_name: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int | None,
) -> ComparisonResult:
    started = time.perf_counter()
    cost_engine = get_cost_engine()

    try:
        provider = get_provider(provider_name)
        response = await provider.chat(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except ProviderError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return ComparisonResult(
            provider=provider_name,
            model=model_name,
            status="failed",
            error=exc.message,
            upstream_status=exc.upstream_status,
            latency_ms=elapsed,
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    estimate = cost_engine.estimate(
        provider=provider_name,
        model=model_name,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
    )

    return ComparisonResult(
        provider=provider_name,
        model=model_name,
        status="succeeded",
        content=response.content,
        latency_ms=elapsed,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        estimated_usd=estimate.estimated_usd,
        input_rate_per_million=estimate.input_rate_per_million,
        output_rate_per_million=estimate.output_rate_per_million,
        pricing_known=estimate.pricing_known,
    )


async def compare_request(request: ChatCompareRequest) -> ChatCompareResponse:
    if request.candidates is not None:
        candidates = [(c["provider"], c["model"]) for c in request.candidates]
    else:
        candidates = _candidates_from_config()

    messages_dict = [m.model_dump() for m in request.messages]

    results = await asyncio.gather(
        *(
            _run_one(p, m, messages_dict, request.temperature, request.max_tokens)
            for p, m in candidates
        )
    )

    succeeded = [r for r in results if r.status == "succeeded"]
    priced = [r for r in succeeded if r.estimated_usd is not None]
    cheapest = min(priced, key=lambda r: r.estimated_usd) if priced else None  # type: ignore[arg-type]
    fastest = min(succeeded, key=lambda r: r.latency_ms) if succeeded else None
    total_usd = sum((r.estimated_usd or 0.0) for r in succeeded)

    return ChatCompareResponse(
        results=list(results),
        cheapest=cheapest,
        fastest=fastest,
        total_estimated_usd=total_usd,
    )
