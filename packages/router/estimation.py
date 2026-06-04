"""Approximate token counting for pre-flight cost estimation.

We don't want each provider's exact tokenizer as a hard dep (tiktoken is OpenAI-
specific; sentencepiece varies per model). For a *preview* the rule-of-thumb
estimate is enough — overshoots slightly, which is the safer direction for cost.

Rule: max(chars/4, words * 1.3). English-tuned; under-counts for CJK languages.
"""
from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Approximate token count for the given text. Returns 0 for empty input."""
    if not text:
        return 0
    chars_estimate = len(text) / 4.0
    words_estimate = len(text.split()) * 1.3
    return max(1, math.ceil(max(chars_estimate, words_estimate)))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Sum of token estimates across all message contents."""
    return sum(estimate_tokens(m.get("content", "")) for m in messages)
