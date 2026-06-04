"""Chat completion orchestration.

The route layer is intentionally thin: it parses the request, then hands it to
`ChatService.create_completion()`, which classifies the task, picks a provider
via the decision engine, walks the fallback chain on retryable errors, and
assembles an OpenAI-compatible response with an extra `routing` block (which
includes a per-attempt breakdown).
"""
from __future__ import annotations

import json
import time
import uuid
from time import perf_counter
from typing import AsyncIterator

from fastapi import HTTPException

from app.api.deps import (
    get_classifier,
    get_cost_engine,
    get_decision_engine,
    get_health_tracker,
    get_provider,
    get_semantic_cache,
)
from app.core.settings import settings
from app.db.models import RequestLog
from app.db.session import get_sessionmaker
from app.metrics import observe_cache, observe_cost, observe_provider_call, observe_request
from app.services.budget_service import assert_under_daily_cap, is_in_soft_cap


def _reject_client_model_if_disabled(request_model: str | None) -> None:
    """Raise 400 when the client supplied `model:` and overrides are off."""
    if request_model and not settings.allow_client_model_override:
        raise HTTPException(
            status_code=400,
            detail={
                "type": "model_override_disabled",
                "message": (
                    "This server rejects client-supplied `model` fields. "
                    "Routing is decided by the configured policy."
                ),
            },
        )
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    CostInfo,
    RiskInfo,
    RoutingAttempt,
    RoutingInfo,
)
from router import risk as risk_guard
from router.errors import ProviderError
from router.logger import get_logger
from router.schemas import ProviderResponse

log = get_logger("llm-router.chat")


async def _write_log(
    request_id: str,
    task_type: str | None,
    selected_provider: str | None,
    selected_model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    estimated_usd: float | None,
    status: str,
    attempts: list,
    error_message: str | None,
    channel: str | None = None,
    routing_reason: str | None = None,
    cohort: str | None = None,
) -> None:
    """Write a single RequestLog row. Never fails the request — logs an error and moves on."""
    try:
        sessionmaker = get_sessionmaker()
    except RuntimeError:
        # DB engine wasn't initialized (e.g. in some test paths). Skip silently.
        return
    try:
        async with sessionmaker() as session:
            session.add(
                RequestLog(
                    request_id=request_id,
                    task_type=task_type,
                    selected_provider=selected_provider,
                    selected_model=selected_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    estimated_usd=estimated_usd,
                    status=status,
                    attempts=attempts,
                    error_message=error_message,
                    channel=channel,
                    routing_reason=routing_reason,
                    cohort=cohort,
                )
            )
            await session.commit()
    except Exception as exc:  # don't bring down the request on log failures
        log.error("failed to write request log: %s", exc)


def _sse(data: dict | str) -> str:
    """Format one SSE event. Pass `"[DONE]"` for the terminator."""
    if data == "[DONE]":
        return "data: [DONE]\n\n"
    return f"data: {json.dumps(data)}\n\n"


class ChatService:
    """Stateless orchestrator: classify → decide → try chain → respond."""

    async def create_completion_stream(
        self,
        request: ChatCompletionRequest,
        request_id: str = "-",
    ) -> AsyncIterator[str]:
        """Streaming path. No fallback chain — only primary provider is tried.

        Yields SSE events:
          - One `data: {OpenAI-shaped delta}` per content chunk.
          - One `data: {"x_smart_router_meta": true, "routing": ..., "cost": ..., "usage": ...}` final metadata.
          - One `data: [DONE]` terminator.
        """
        _reject_client_model_if_disabled(request.model)
        await assert_under_daily_cap()
        classifier = get_classifier()
        decision_engine = get_decision_engine()
        messages_as_dict = [m.model_dump() for m in request.messages]

        classified_task = classifier.classify(messages_as_dict)
        soft_cap = await is_in_soft_cap()
        decision = decision_engine.decide(
            classified_task=classified_task,
            request_model=request.model,
            metadata=request.metadata,
            budget_soft_cap=soft_cap,
        )
        used_provider = decision.provider
        used_model = decision.model
        if decision.provider == "manual":
            used_provider = "mock"
            used_model = request.model or "manual-model"

        provider = get_provider(used_provider)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_ts = int(time.time())
        final_response: ProviderResponse | None = None
        error_message: str | None = None

        try:
            async for chunk_text, final in provider.stream(
                model=used_model,
                messages=messages_as_dict,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            ):
                if chunk_text:
                    yield _sse(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": used_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": chunk_text},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                if final is not None:
                    final_response = final
        except ProviderError as exc:
            error_message = exc.message
            log.warning(
                "[%s] stream failed provider=%s model=%s upstream=%s msg=%r",
                request_id,
                used_provider,
                used_model,
                exc.upstream_status,
                exc.message,
            )
            yield _sse(
                {
                    "error": {
                        "type": "provider_error",
                        "provider": exc.provider,
                        "message": exc.message,
                        "request_id": request_id,
                    }
                }
            )

        # Finish-reason event (OpenAI-style).
        yield _sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": used_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if final_response else "error",
                    }
                ],
            }
        )

        # Metadata event (router-specific). Standard OpenAI clients ignore unknown fields.
        prompt_tokens = final_response.prompt_tokens if final_response else 0
        completion_tokens = final_response.completion_tokens if final_response else 0
        cost_estimate = get_cost_engine().estimate(
            provider=used_provider,
            model=used_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        attempts_payload = [
            {
                "provider": used_provider,
                "model": used_model,
                "status": "succeeded" if final_response else "failed",
                "upstream_status": None,
                "error": error_message,
            }
        ]
        meta_event = {
            "x_smart_router_meta": True,
            "id": completion_id,
            "model": used_model,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "routing": {
                "task_type": decision.task_type,
                "selected_provider": used_provider,
                "selected_model": used_model,
                "reason": decision.reason,
                "attempts": attempts_payload,
            },
            "cost": {
                "currency": "USD",
                "estimated_usd": cost_estimate.estimated_usd,
                "input_usd": cost_estimate.input_usd,
                "output_usd": cost_estimate.output_usd,
                "input_rate_per_million": cost_estimate.input_rate_per_million,
                "output_rate_per_million": cost_estimate.output_rate_per_million,
                "pricing_known": cost_estimate.pricing_known,
            },
        }
        yield _sse(meta_event)
        yield _sse("[DONE]")

        await _write_log(
            request_id=request_id,
            task_type=decision.task_type,
            selected_provider=used_provider,
            selected_model=used_model,
            prompt_tokens=prompt_tokens if final_response else None,
            completion_tokens=completion_tokens if final_response else None,
            estimated_usd=cost_estimate.estimated_usd if final_response else None,
            status="succeeded" if final_response else "failed",
            attempts=attempts_payload,
            error_message=error_message,
            channel=(request.metadata or {}).get("channel") if request.metadata else None,
            routing_reason=decision.reason,
            cohort=decision.cohort,
        )

    async def create_completion(
        self,
        request: ChatCompletionRequest,
        request_id: str = "-",
    ) -> ChatCompletionResponse:
        _reject_client_model_if_disabled(request.model)
        await assert_under_daily_cap()
        classifier = get_classifier()
        decision_engine = get_decision_engine()

        messages_as_dict = [message.model_dump() for message in request.messages]

        classified_task = classifier.classify(messages_as_dict)
        soft_cap = await is_in_soft_cap()

        # Prompt Risk Guard runs before routing so it can influence the decision
        # (override to safe_provider) and after-the-fact effects (cache bypass).
        risk_assessment = (
            risk_guard.assess_messages(messages_as_dict)
            if settings.enable_risk_guard
            else risk_guard.RiskAssessment(triggered=False, categories=(), patterns_matched=())
        )

        decision = decision_engine.decide(
            classified_task=classified_task,
            request_model=request.model,
            metadata=request.metadata,
            budget_soft_cap=soft_cap,
            risk_triggered=risk_assessment.triggered,
        )

        # `provider="manual"` is the DecisionEngine's signal that the caller
        # explicitly requested a model. In Phase 1 we route those through the
        # mock provider so we never accidentally bill an upstream API on an
        # unvalidated client-supplied model.
        primary_provider = decision.provider
        primary_model = decision.model
        if decision.provider == "manual":
            primary_provider = "mock"
            primary_model = request.model or "manual-model"

        candidates: list[tuple[str, str]] = [(primary_provider, primary_model)]
        candidates.extend(decision.fallbacks)

        attempts: list[RoutingAttempt] = []
        provider_response: ProviderResponse | None = None
        used_provider: str = primary_provider
        used_model: str = primary_model
        last_error: ProviderError | None = None
        cache_hit = False

        # Track which Risk Guard actions we took for the response body.
        risk_actions: list[str] = []
        if risk_assessment.triggered:
            risk_actions.append("cache_bypassed")
            if "risk override" in decision.reason:
                risk_actions.append("rerouted_to_safe_provider")

        # Semantic cache lookup. Skipped entirely when the prompt tripped the
        # Risk Guard — never serve a cached response for a sensitive request.
        cache_eligible = decision.cache_enabled and not risk_assessment.triggered
        sem_cache = get_semantic_cache() if cache_eligible else None
        prompt_for_cache = messages_as_dict[-1].get("content", "") if messages_as_dict else ""
        if sem_cache is not None and prompt_for_cache:
            cached = sem_cache.lookup(prompt_for_cache, primary_model)
            if cached is not None:
                provider_response = cached
                used_provider = primary_provider
                used_model = primary_model
                cache_hit = True
                attempts.append(
                    RoutingAttempt(
                        provider=primary_provider,
                        model=primary_model,
                        status="succeeded",
                    )
                )
                observe_cache("hit")
                log.info(
                    "[%s] cache hit provider=%s model=%s task_type=%s",
                    request_id,
                    primary_provider,
                    primary_model,
                    decision.task_type,
                )
            else:
                observe_cache("miss")

        health_tracker = get_health_tracker()
        for provider_name, model_name in (candidates if not cache_hit else []):
            started = perf_counter()
            try:
                provider = get_provider(provider_name)
                provider_response = await provider.chat(
                    model=model_name,
                    messages=messages_as_dict,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
                latency = perf_counter() - started
                observe_provider_call(provider_name, model_name, "succeeded", latency)
                health_tracker.record(provider_name, success=True, latency_ms=latency * 1000.0)
                attempts.append(
                    RoutingAttempt(
                        provider=provider_name,
                        model=model_name,
                        status="succeeded",
                    )
                )
                used_provider = provider_name
                used_model = model_name
                log.info(
                    "[%s] succeeded provider=%s model=%s task_type=%s",
                    request_id,
                    provider_name,
                    model_name,
                    decision.task_type,
                )
                break
            except ProviderError as exc:
                latency = perf_counter() - started
                observe_provider_call(provider_name, model_name, "failed", latency)
                health_tracker.record(provider_name, success=False, latency_ms=latency * 1000.0)
                attempts.append(
                    RoutingAttempt(
                        provider=provider_name,
                        model=model_name,
                        status="failed",
                        upstream_status=exc.upstream_status,
                        error=exc.message,
                    )
                )
                log.warning(
                    "[%s] failed provider=%s model=%s upstream=%s msg=%r retryable=%s",
                    request_id,
                    provider_name,
                    model_name,
                    exc.upstream_status,
                    exc.message,
                    exc.is_retryable(),
                )
                last_error = exc
                if not exc.is_retryable():
                    # 4xx (bad request, auth) — fallback won't help.
                    break

        if provider_response is None:
            # All candidates exhausted or non-retryable error — log + surface.
            assert last_error is not None
            observe_request("/v1/chat/completions", "failed")
            await _write_log(
                request_id=request_id,
                task_type=classified_task.task_type,
                selected_provider=last_error.provider,
                selected_model=attempts[-1].model if attempts else None,
                prompt_tokens=None,
                completion_tokens=None,
                estimated_usd=None,
                status="failed",
                attempts=[a.model_dump() for a in attempts],
                error_message=last_error.message,
                channel=(request.metadata or {}).get("channel") if request.metadata else None,
                routing_reason=decision.reason,
                cohort=decision.cohort,
            )
            raise last_error

        total_tokens = provider_response.prompt_tokens + provider_response.completion_tokens

        # Build a human-readable reason that reflects what actually happened.
        if len(attempts) == 1:
            final_reason = decision.reason
        else:
            failed_names = ", ".join(a.provider for a in attempts[:-1])
            final_reason = (
                f"{decision.reason} | recovered via fallback after {failed_names} failed"
            )

        # Store in semantic cache on a fresh (non-hit) success.
        if not cache_hit and sem_cache is not None and prompt_for_cache:
            sem_cache.store(prompt_for_cache, used_model, provider_response)
            observe_cache("store")

        cost_estimate = get_cost_engine().estimate(
            provider=used_provider,
            model=used_model,
            prompt_tokens=provider_response.prompt_tokens,
            completion_tokens=provider_response.completion_tokens,
        )
        cost = CostInfo(
            estimated_usd=cost_estimate.estimated_usd,
            input_usd=cost_estimate.input_usd,
            output_usd=cost_estimate.output_usd,
            input_rate_per_million=cost_estimate.input_rate_per_million,
            output_rate_per_million=cost_estimate.output_rate_per_million,
            pricing_known=cost_estimate.pricing_known,
        )

        observe_cost(used_provider, used_model, cost_estimate.estimated_usd)
        observe_request("/v1/chat/completions", "succeeded")

        await _write_log(
            request_id=request_id,
            task_type=decision.task_type,
            selected_provider=used_provider,
            selected_model=used_model,
            prompt_tokens=provider_response.prompt_tokens,
            completion_tokens=provider_response.completion_tokens,
            estimated_usd=cost_estimate.estimated_usd,
            status="succeeded",
            attempts=[a.model_dump() for a in attempts],
            error_message=None,
            channel=(request.metadata or {}).get("channel") if request.metadata else None,
            routing_reason=final_reason,
            cohort=decision.cohort,
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=used_model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionChoiceMessage(content=provider_response.content),
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=provider_response.prompt_tokens,
                completion_tokens=provider_response.completion_tokens,
                total_tokens=total_tokens,
            ),
            routing=RoutingInfo(
                task_type=decision.task_type,
                selected_provider=used_provider,
                selected_model=used_model,
                reason=final_reason,
                attempts=attempts,
            ),
            cost=cost,
            risk=(
                RiskInfo(
                    triggered=risk_assessment.triggered,
                    categories=list(risk_assessment.categories),
                    patterns_matched=list(risk_assessment.patterns_matched),
                    actions=risk_actions,
                )
                if settings.enable_risk_guard
                else None
            ),
        )
