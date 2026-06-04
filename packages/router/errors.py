"""Typed exceptions raised by the router package.

These are caught by FastAPI exception handlers in `app/main.py` and mapped to
proper HTTP responses, so callers see a structured error body instead of a
generic 500.
"""
from __future__ import annotations


class RouterError(Exception):
    """Base class for all errors raised by the router package."""


class ConfigError(RouterError):
    """Raised when routing configuration cannot be loaded or is invalid."""


class ProviderError(RouterError):
    """Raised when an upstream LLM provider call fails.

    `status_code` is what the API surfaces to the caller (502 for upstream
    HTTP errors, 503 for network / missing-key issues). `upstream_status` is
    the HTTP status the provider itself returned (None for network failures),
    used by the fallback chain to decide whether retrying a different provider
    is worth it — see `is_retryable()`.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        status_code: int = 502,
        upstream_status: int | None = None,
    ) -> None:
        self.provider = provider
        self.message = message
        self.status_code = status_code
        self.upstream_status = upstream_status
        super().__init__(f"{provider}: {message}")

    def is_retryable(self) -> bool:
        """True if a different provider/model might succeed.

        - Network / timeout / missing-key (no upstream status) → yes
        - 429 (rate-limited) → yes
        - 5xx (server error)  → yes
        - 4xx (bad request, auth, model not found) → no — same input would fail elsewhere too
        """
        if self.upstream_status is None:
            return True
        if self.upstream_status == 429:
            return True
        if 500 <= self.upstream_status < 600:
            return True
        return False
