"""Shared async streaming helper for OpenAI-compatible REST endpoints.

Used by `openai_provider.py`, `groq.py`, and `gemini.py` since all three
expose the same `POST /chat/completions` shape and the same SSE response
format: `data: {json}\\n\\n` per chunk, `data: [DONE]\\n\\n` to finish.

The helper yields tuples of `(delta_text, final_provider_response_or_none)`
so callers can stream tokens to clients and capture final usage at the end.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from router.errors import ProviderError
from router.schemas import ProviderResponse


async def stream_openai_compat(
    provider_name: str,
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0,
) -> AsyncIterator[tuple[str, ProviderResponse | None]]:
    """Stream chat completions from an OpenAI-compatible endpoint.

    Yields one `(delta_text, None)` per content chunk, then a single
    `("", ProviderResponse(...))` carrying the assembled content + token counts
    once the upstream sends `[DONE]`.
    """
    accumulated: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:200]
                    raise ProviderError(
                        provider_name,
                        f"HTTP {response.status_code}: {body}",
                        status_code=502,
                        upstream_status=response.status_code,
                    )
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # OpenAI-compatible: choices[0].delta.content
                    choices = event.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        chunk_text = delta.get("content")
                        if chunk_text:
                            accumulated.append(chunk_text)
                            yield chunk_text, None

                    # Usage typically arrives in a final event for streaming responses.
                    usage = event.get("usage")
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200] if exc.response is not None else ""
        raise ProviderError(
            provider_name,
            f"HTTP {exc.response.status_code}: {body}",
            status_code=502,
            upstream_status=exc.response.status_code,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            provider_name, f"network error: {exc}", status_code=503
        ) from exc

    full_content = "".join(accumulated)
    # Fallback: many providers don't include `usage` in streaming responses.
    # Approximate completion tokens from the content if missing.
    if completion_tokens == 0 and full_content:
        completion_tokens = max(1, len(full_content.split()))
    yield (
        "",
        ProviderResponse(
            content=full_content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )
