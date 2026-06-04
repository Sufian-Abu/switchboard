"""HTTP-facing chat-completion schemas.

Mirrors the public OpenAI `chat.completion` response shape and adds a custom
top-level `routing` block so clients can see *why* a particular provider/model
was chosen.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single message in a chat completion request."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Inbound body for `POST /v1/chat/completions`.

    When `stream=True`, the response is server-sent events (`text/event-stream`).
    Routing + cost metadata are sent as a final event before `data: [DONE]`.
    Fallback chains are not applied to streaming responses — only the primary
    provider is tried, since switching mid-stream would corrupt output.
    """

    model: str | None = None
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] | None = None


class ChatCompletionChoiceMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatCompletionChoiceMessage
    finish_reason: str = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class RoutingAttempt(BaseModel):
    """One entry in the routing attempt chain (primary + each fallback tried)."""

    provider: str
    model: str
    status: Literal["succeeded", "failed"]
    upstream_status: int | None = None
    error: str | None = None


class RoutingInfo(BaseModel):
    """Router-specific metadata attached to every chat completion response."""

    task_type: str
    selected_provider: str
    selected_model: str
    reason: str
    attempts: list[RoutingAttempt] = Field(
        default_factory=list,
        description="Provider/model attempts made — primary first, fallbacks after.",
    )


class ChatEstimateRequest(BaseModel):
    """Inbound body for `POST /v1/chat/estimate` — preview cost across models."""

    messages: list[ChatMessage]
    # Either: explicit candidate list, OR null = estimate for every priced model.
    candidates: list[dict[str, str]] | None = None
    # Assumed upper bound on completion length when the caller hasn't decided.
    assumed_max_tokens: int = 256


class ChatRouteRequest(BaseModel):
    """Inbound body for `POST /v1/chat/route` — classify + decide, no provider call."""

    messages: list[ChatMessage]
    model: str | None = None


class ChatRouteResponse(BaseModel):
    """Routing decision for the given prompt — what Switchboard *would* do."""

    task_type: str
    task_reason: str
    selected_provider: str
    selected_model: str
    reason: str
    fallbacks: list[dict[str, str]]
    cache_enabled: bool


class ChatCompareRequest(BaseModel):
    """Inbound body for `POST /v1/chat/compare` — run the same prompt across N models in parallel."""

    messages: list[ChatMessage]
    # Explicit list of (provider, model) to compare. If null, every priced model
    # in the routing config is used.
    candidates: list[dict[str, str]] | None = None
    temperature: float = 0.7
    max_tokens: int | None = 200


class ComparisonResult(BaseModel):
    """One model's response in a compare-all run."""

    provider: str
    model: str
    status: Literal["succeeded", "failed"]
    content: str | None = None
    error: str | None = None
    upstream_status: int | None = None
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_usd: float | None = None
    input_rate_per_million: float | None = None
    output_rate_per_million: float | None = None
    pricing_known: bool = False


class ChatCompareResponse(BaseModel):
    """Response for `POST /v1/chat/compare`."""

    results: list[ComparisonResult]
    cheapest: ComparisonResult | None = None
    fastest: ComparisonResult | None = None
    total_estimated_usd: float


class ModelEstimate(BaseModel):
    """Cost preview for one candidate provider+model."""

    provider: str
    model: str
    input_tokens_estimated: int
    assumed_max_completion_tokens: int
    estimated_usd_min: float | None = Field(
        default=None,
        description="USD cost for input tokens only (lower bound).",
    )
    estimated_usd_max: float | None = Field(
        default=None,
        description="USD cost assuming the assumed_max_completion_tokens fully used.",
    )
    input_rate_per_million: float | None = None
    output_rate_per_million: float | None = None
    pricing_known: bool = False


class ChatEstimateResponse(BaseModel):
    """Response for `POST /v1/chat/estimate`."""

    input_tokens_estimated: int
    assumed_max_completion_tokens: int
    estimates: list[ModelEstimate]
    cheapest: ModelEstimate | None = None
    most_expensive: ModelEstimate | None = None


class CostInfo(BaseModel):
    """USD cost estimate for the served call (only for the successful attempt).

    `pricing_known=false` means the pricing table had no entry for the
    selected provider+model — the USD fields will all be null.
    """

    currency: Literal["USD"] = "USD"
    estimated_usd: float | None = None
    input_usd: float | None = None
    output_usd: float | None = None
    input_rate_per_million: float | None = None
    output_rate_per_million: float | None = None
    pricing_known: bool = False


class ChatCompletionResponse(BaseModel):
    """Outbound body for `POST /v1/chat/completions`. OpenAI-compatible + `routing` + `cost`."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage
    routing: RoutingInfo = Field(..., description="Custom routing metadata")
    cost: CostInfo = Field(
        default_factory=lambda: CostInfo(),
        description="USD cost estimate for the successful provider call.",
    )
