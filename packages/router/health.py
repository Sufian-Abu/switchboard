"""Provider health tracking — the headline differentiator.

Maintains a rolling window of recent provider call outcomes (success/failure
+ latency) and classifies each provider as healthy / degraded / unhealthy.
The DecisionEngine consults this before selecting a candidate so unhealthy
providers can be preemptively skipped — not just retried-around when they
fail an individual request.

Why this matters:
  - LiteLLM / OpenRouter / Portkey don't do preemptive avoidance.
  - A 30-second-timeout from a provider that's been down for 10 minutes
    is wasted user latency even when fallback eventually kicks in.
  - Surfacing the health state in the dashboard ("Groq has been degraded
    for 4 minutes") gives ops a real signal.

The tracker is in-process and reset on restart. For multi-instance
deployments this should be replaced with a shared store (Redis); that's
on the roadmap.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Literal

HealthBand = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True)
class CallOutcome:
    """A single recorded provider call."""

    ts: float           # epoch seconds
    success: bool       # True if the upstream returned a usable response
    latency_ms: float


@dataclass
class HealthSnapshot:
    """Public view of a provider's recent health."""

    provider: str
    band: HealthBand
    sample_size: int
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    window_seconds: int

    def is_avoidable(self, threshold: HealthBand) -> bool:
        """Should the decision engine skip this provider given a rule's avoid_if_health threshold?"""
        order = {"healthy": 0, "degraded": 1, "unhealthy": 2}
        return order[self.band] >= order[threshold]


@dataclass
class _BandThresholds:
    """The cutoffs that decide which band a provider falls into."""

    error_rate_degraded: float = 0.05
    error_rate_unhealthy: float = 0.25
    latency_p95_degraded_ms: float = 5000.0
    latency_p95_unhealthy_ms: float = 15000.0
    min_samples: int = 5  # below this we report "healthy" (not enough signal to penalise)


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    # Linear interpolation between closest ranks; good enough for ops UX.
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


class ProviderHealthTracker:
    """Rolling-window health stats per provider. Thread-safe."""

    def __init__(
        self,
        window_seconds: int = 300,           # 5 minutes
        max_samples_per_provider: int = 200,  # cap memory growth
        thresholds: _BandThresholds | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_samples_per_provider = max_samples_per_provider
        self.thresholds = thresholds or _BandThresholds()
        self._lock = threading.Lock()
        self._samples: dict[str, deque[CallOutcome]] = {}

    def record(self, provider: str, success: bool, latency_ms: float) -> None:
        """Record a single provider call outcome."""
        with self._lock:
            bucket = self._samples.setdefault(
                provider, deque(maxlen=self.max_samples_per_provider)
            )
            bucket.append(CallOutcome(ts=time(), success=success, latency_ms=latency_ms))

    def snapshot(self, provider: str) -> HealthSnapshot:
        """Compute the current snapshot for one provider."""
        with self._lock:
            bucket = self._samples.get(provider)
            relevant = self._recent(bucket) if bucket else []

        if len(relevant) < self.thresholds.min_samples:
            return HealthSnapshot(
                provider=provider,
                band="healthy",
                sample_size=len(relevant),
                error_rate=0.0,
                latency_p50_ms=0.0,
                latency_p95_ms=0.0,
                window_seconds=self.window_seconds,
            )

        errors = sum(1 for c in relevant if not c.success)
        error_rate = errors / len(relevant)
        latencies = sorted(c.latency_ms for c in relevant)
        p50 = _percentile(latencies, 0.5)
        p95 = _percentile(latencies, 0.95)

        band: HealthBand = "healthy"
        t = self.thresholds
        if error_rate >= t.error_rate_unhealthy or p95 >= t.latency_p95_unhealthy_ms:
            band = "unhealthy"
        elif error_rate >= t.error_rate_degraded or p95 >= t.latency_p95_degraded_ms:
            band = "degraded"

        return HealthSnapshot(
            provider=provider,
            band=band,
            sample_size=len(relevant),
            error_rate=error_rate,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            window_seconds=self.window_seconds,
        )

    def snapshot_all(self) -> list[HealthSnapshot]:
        """One snapshot per provider we've ever seen."""
        with self._lock:
            providers = list(self._samples.keys())
        return [self.snapshot(p) for p in providers]

    def _recent(self, bucket: deque[CallOutcome]) -> list[CallOutcome]:
        cutoff = time() - self.window_seconds
        return [c for c in bucket if c.ts >= cutoff]
