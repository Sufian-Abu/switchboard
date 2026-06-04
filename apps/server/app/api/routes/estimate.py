"""POST /v1/chat/estimate — pre-flight cost preview across candidate models."""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.chat import ChatEstimateRequest, ChatEstimateResponse
from app.services.estimate_service import estimate_request

router = APIRouter()


@router.post("/v1/chat/estimate", response_model=ChatEstimateResponse)
async def chat_estimate(body: ChatEstimateRequest) -> ChatEstimateResponse:
    return estimate_request(body)
