"""Ollama (local LLM runtime) provider.

Calls Ollama's `/api/chat` endpoint. Errors are translated into `ProviderError`
so a missing daemon or unloaded model surfaces as a 503 instead of a 500.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from router.errors import ProviderError
from router.providers.base import BaseProvider
from router.schemas import ProviderResponse


class OllamaProvider(BaseProvider):
    """Calls `POST {base_url}/api/chat` (non-streaming)."""

    name = "ollama"

    def __init__(self, base_url: str) -> None:
        self.base_url = f"{base_url.rstrip('/')}/api/chat"

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(self.base_url, json=payload)
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
            raise ProviderError(self.name, f"network error: {exc}", status_code=503) from exc

        content = data.get("message", {}).get("content", "")
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        return ProviderResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[str, ProviderResponse | None]]:
        """Ollama streams newline-delimited JSON, not SSE. Parse each line as an event."""
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        accumulated: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", self.base_url, json=payload) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")[:200]
                        raise ProviderError(
                            self.name,
                            f"HTTP {response.status_code}: {body}",
                            status_code=502,
                            upstream_status=response.status_code,
                        )
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk = event.get("message", {}).get("content", "")
                        if chunk:
                            accumulated.append(chunk)
                            yield chunk, None
                        if event.get("done"):
                            prompt_tokens = event.get("prompt_eval_count", prompt_tokens)
                            completion_tokens = event.get("eval_count", completion_tokens)
                            break
        except httpx.RequestError as exc:
            raise ProviderError(self.name, f"network error: {exc}", status_code=503) from exc

        yield (
            "",
            ProviderResponse(
                content="".join(accumulated),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        )
