"""Prometheus metrics for Switchboard.

Optional. Requires the `metrics` extra (`pip install -e ".[metrics]"`). When
the dependency is missing, `is_enabled()` returns False and all `observe_*`
calls are no-ops, so the rest of the app stays clean.

Exposed at `GET /metrics`. Scrape it from Prometheus, push it through
Grafana Agent, whatever you already use.
"""
from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
    )

    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain"  # type: ignore[assignment]

    def generate_latest(*_a, **_kw) -> bytes:  # type: ignore[no-redef]
        return b""

    CollectorRegistry = Counter = Histogram = None  # type: ignore[misc,assignment]


def is_enabled() -> bool:
    return _AVAILABLE


if _AVAILABLE:
    _registry = CollectorRegistry()

    requests_total = Counter(
        "switchboard_requests_total",
        "Total /v1/chat/* requests handled.",
        ["endpoint", "status"],
        registry=_registry,
    )
    provider_calls_total = Counter(
        "switchboard_provider_calls_total",
        "Provider calls made (one per attempt, including fallbacks).",
        ["provider", "model", "status"],
        registry=_registry,
    )
    provider_latency_seconds = Histogram(
        "switchboard_provider_latency_seconds",
        "Provider call latency.",
        ["provider"],
        registry=_registry,
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
    )
    cost_usd_total = Counter(
        "switchboard_cost_usd_total",
        "Cumulative estimated USD spend by provider and model.",
        ["provider", "model"],
        registry=_registry,
    )
    cache_lookups_total = Counter(
        "switchboard_cache_lookups_total",
        "Semantic cache lookups by outcome.",
        ["outcome"],
        registry=_registry,
    )
else:
    _registry = None
    requests_total = None
    provider_calls_total = None
    provider_latency_seconds = None
    cost_usd_total = None
    cache_lookups_total = None


def observe_request(endpoint: str, status: str) -> None:
    if _AVAILABLE:
        requests_total.labels(endpoint=endpoint, status=status).inc()


def observe_provider_call(provider: str, model: str, status: str, latency_s: float) -> None:
    if _AVAILABLE:
        provider_calls_total.labels(provider=provider, model=model, status=status).inc()
        provider_latency_seconds.labels(provider=provider).observe(latency_s)


def observe_cost(provider: str, model: str, usd: float | None) -> None:
    if _AVAILABLE and usd is not None and usd > 0:
        cost_usd_total.labels(provider=provider, model=model).inc(usd)


def observe_cache(outcome: str) -> None:
    """Outcome is one of: 'hit', 'miss', 'store', 'eviction'."""
    if _AVAILABLE:
        cache_lookups_total.labels(outcome=outcome).inc()


@contextmanager
def time_provider_call(provider: str):
    """Context manager that records latency to `provider_latency_seconds`."""
    started = perf_counter()
    yield
    if _AVAILABLE:
        provider_latency_seconds.labels(provider=provider).observe(perf_counter() - started)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content-type) for the /metrics endpoint."""
    if not _AVAILABLE:
        body = (
            b"# Switchboard metrics are disabled. Install the optional dependency:\n"
            b"#   pip install -e \".[metrics]\"\n"
        )
        return body, "text/plain; version=0.0.4"
    return generate_latest(_registry), CONTENT_TYPE_LATEST
