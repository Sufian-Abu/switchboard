"""Cost estimation.

Computes a USD estimate per call by looking up the provider+model in a
pricing table (loaded from `configs/pricing.yaml`) and multiplying by the
provider-reported token counts. Returns `None` fields when pricing is not
configured for a given provider+model rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CostEstimate:
    """USD cost breakdown for a single chat completion."""

    estimated_usd: float | None
    input_usd: float | None
    output_usd: float | None
    input_rate_per_million: float | None
    output_rate_per_million: float | None
    pricing_known: bool


class CostEngine:
    """Looks up per-model pricing and estimates USD cost from token counts.

    Lookup order for `<provider>.<model>`:
      1. Exact match: pricing[provider][model]
      2. Wildcard match: pricing[provider]["*"]
      3. Miss → returns CostEstimate(pricing_known=False) with None fields.
    """

    def __init__(self, pricing: dict[str, Any]) -> None:
        self.pricing = pricing or {}

    def estimate(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> CostEstimate:
        rates = self._rates_for(provider, model)
        if rates is None:
            return CostEstimate(
                estimated_usd=None,
                input_usd=None,
                output_usd=None,
                input_rate_per_million=None,
                output_rate_per_million=None,
                pricing_known=False,
            )

        input_rate = float(rates.get("input", 0))
        output_rate = float(rates.get("output", 0))
        input_usd = prompt_tokens * input_rate / 1_000_000
        output_usd = completion_tokens * output_rate / 1_000_000
        return CostEstimate(
            estimated_usd=input_usd + output_usd,
            input_usd=input_usd,
            output_usd=output_usd,
            input_rate_per_million=input_rate,
            output_rate_per_million=output_rate,
            pricing_known=True,
        )

    def _rates_for(self, provider: str, model: str) -> dict | None:
        provider_table = self.pricing.get(provider)
        if not provider_table:
            return None
        if model in provider_table:
            return provider_table[model]
        if "*" in provider_table:
            return provider_table["*"]
        return None
