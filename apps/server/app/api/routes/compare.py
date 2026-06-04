"""POST /v1/chat/compare — run the same prompt across N models in parallel."""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.chat import ChatCompareRequest, ChatCompareResponse
from app.services.compare_service import compare_request

router = APIRouter()


@router.post("/v1/chat/compare", response_model=ChatCompareResponse)
async def chat_compare(body: ChatCompareRequest) -> ChatCompareResponse:
    return await compare_request(body)
