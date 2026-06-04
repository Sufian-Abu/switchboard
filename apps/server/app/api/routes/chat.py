"""POST /v1/chat/completions — OpenAI-compatible chat endpoint (streaming and non-streaming).
Also POST /v1/chat/route — preview the routing decision without calling any provider.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_classifier, get_decision_engine
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatRouteRequest,
    ChatRouteResponse,
)
from app.services.chat_service import ChatService, _reject_client_model_if_disabled

router = APIRouter()


@router.post("/v1/chat/route", response_model=ChatRouteResponse)
async def chat_route(body: ChatRouteRequest) -> ChatRouteResponse:
    """Classify the prompt and return the routing decision. No upstream call."""
    # Defence in depth: the routing-only preview honours the same policy as
    # /v1/chat/completions. A client that's been blocked from overriding the
    # model on real requests shouldn't be able to inspect what the override
    # would have routed to either.
    _reject_client_model_if_disabled(body.model)
    classifier = get_classifier()
    engine = get_decision_engine()
    messages = [m.model_dump() for m in body.messages]
    classified = classifier.classify(messages)
    decision = engine.decide(classified_task=classified, request_model=body.model)
    return ChatRouteResponse(
        task_type=classified.task_type,
        task_reason=classified.reason,
        selected_provider=decision.provider,
        selected_model=decision.model,
        reason=decision.reason,
        fallbacks=[{"provider": p, "model": m} for p, m in decision.fallbacks],
        cache_enabled=decision.cache_enabled,
    )


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: Request,
    body: ChatCompletionRequest,
):
    # Validate at the route level so streaming requests get a clean 400
    # response instead of an SSE stream that opens then errors mid-flight.
    _reject_client_model_if_disabled(body.model)
    request_id = getattr(request.state, "request_id", "-")
    service = ChatService()
    if body.stream:
        return StreamingResponse(
            service.create_completion_stream(body, request_id=request_id),
            media_type="text/event-stream",
            headers={"x-request-id": request_id, "cache-control": "no-cache"},
        )
    return await service.create_completion(body, request_id=request_id)


# Keep response model attached to the OpenAPI schema for non-streaming responses,
# without forcing all responses through Pydantic validation (which can't validate
# a StreamingResponse).
create_chat_completion.__annotations__["return"] = ChatCompletionResponse
