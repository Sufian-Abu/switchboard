"""Provider abstraction.

Every concrete LLM provider implements:
  - `chat()` — non-streaming, returns a final `ProviderResponse`.
  - `stream()` — async generator yielding (content_chunk, usage_so_far) tuples.
    `usage_so_far` may be `None` for intermediate chunks; should be a real value
    on the final chunk so the orchestrator can record tokens and cost.

Keeping the surface this small makes the decision engine completely
provider-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from router.schemas import ProviderResponse


class BaseProvider(ABC):
    """Abstract chat provider. Subclasses must implement `chat()` and `stream()`."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        """Send a chat completion request and return the parsed response."""
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[str, "ProviderResponse | None"]]:
        """Async generator yielding (delta_text, final_response_or_none).

        Intermediate yields are `(chunk_text, None)`. The final yield should
        be `("", ProviderResponse(...))` carrying the full content + token
        counts so the orchestrator can build a usage block.
        """
        raise NotImplementedError
