"""POST /v1/chat/completions — OpenAI-compatible chat endpoint (streaming and non-streaming)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: Request,
    body: ChatCompletionRequest,
):
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
