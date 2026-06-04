"""OpenAI chat completions provider.

Thin async wrapper over the OpenAI REST API using httpx. Errors are translated
into `ProviderError` so they surface as proper HTTP responses rather than 500s.
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx

from router.errors import ProviderError
from router.providers._openai_compat import stream_openai_compat
from router.providers.base import BaseProvider
from router.schemas import ProviderResponse


class OpenAIProvider(BaseProvider):
    """Calls `POST https://api.openai.com/v1/chat/completions`."""

    name = "openai"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.base_url = "https://api.openai.com/v1/chat/completions"

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(self.name, "OPENAI_API_KEY is missing", status_code=503)

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(self.base_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200] if exc.response is not None else ""
            raise ProviderError(
                self.name,
                f"HTTP {exc.response.status_code}: {body}",
                status_code=502,
                upstream_status=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            # Network / DNS / timeout failure. No upstream_status — treated as retryable.
            raise ProviderError(self.name, f"network error: {exc}", status_code=503) from exc

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return ProviderResponse(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[str, ProviderResponse | None]]:
        if not self.api_key:
            raise ProviderError(self.name, "OPENAI_API_KEY is missing", status_code=503)
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async for chunk in stream_openai_compat(self.name, self.base_url, headers, payload):
            yield chunk
