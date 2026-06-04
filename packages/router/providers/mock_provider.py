"""Mock provider — echoes the last user message with a tagged prefix.

Used as the safe default in Phase 1 so the full request path works with no
API keys, no network calls, and deterministic output. Token counts are
approximated via whitespace split — accurate enough for smoke tests, not for
billing.

`stream()` chunks the response word-by-word with a tiny async sleep so
streaming downstream behaves like a real provider.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from router.providers.base import BaseProvider
from router.schemas import ProviderResponse


class MockProvider(BaseProvider):
    """Returns a synthetic response containing the last user message."""

    name = "mock"

    def _build_response(self, model: str, messages: list[dict]) -> ProviderResponse:
        last_user_message = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user_message = message.get("content", "")
                break
        content = f"[MOCK RESPONSE via {model}] You said: {last_user_message}"
        prompt_tokens = sum(len(m.get("content", "").split()) for m in messages)
        completion_tokens = len(content.split())
        return ProviderResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        return self._build_response(model, messages)

    async def stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[str, "ProviderResponse | None"]]:
        full = self._build_response(model, messages)
        for word in full.content.split():
            await asyncio.sleep(0.005)  # simulate token cadence
            yield word + " ", None
        yield "", full
