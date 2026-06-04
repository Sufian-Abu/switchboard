"""Prompt Risk Guard — detect sensitive content before routing.

A v1 heuristic detector for the kinds of prompts that need different
handling: PII, medical advice, legal advice, financial advice. When the
guard fires, the orchestrator can:

  - Bypass the semantic cache (so one user's response isn't served to
    another with a similar prompt).
  - Force routing to a rule's `safe_provider` / `safe_model` if set.
  - Surface a `risk` block in the response so the caller knows what was
    detected and what we did about it.

This is intentionally a coarse heuristic — fast, no ML, no external
service. Good enough to defend the dangerous edge of the semantic cache
warning. A v2 could plug in a small NER model under the same interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# --- Patterns ---------------------------------------------------------------


_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn":         re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "email":       re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone":       re.compile(r"\b\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b"),
    "ip_address":  re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    # Common API key / token prefixes — catch leaked secrets in prompts.
    "api_key":     re.compile(r"\b(?:sk-|pk-|ghp_|gho_|xoxb-)[A-Za-z0-9_]{8,}\b"),
}

# Word-list heuristics. Lowercased; matched as standalone tokens. Conservative
# lists tuned to fire on *advice-seeking* prompts in these domains, not on
# every mention of the topic.
_MEDICAL_TERMS: frozenset[str] = frozenset({
    "diagnosis", "symptoms", "prescription", "dose", "dosage", "medication",
    "treatment", "side effect", "side effects", "mg/kg", "prescribed",
    "should i take", "should i stop taking", "is this safe to take",
    "medical advice",
})
_LEGAL_TERMS: frozenset[str] = frozenset({
    "lawsuit", "sue", "litigation", "settlement", "court order", "subpoena",
    "indictment", "non-disclosure agreement", "nda", "contract review",
    "can i be liable", "legal advice", "represented by counsel",
})
_FINANCIAL_TERMS: frozenset[str] = frozenset({
    "should i invest", "should i sell", "tax shelter", "evade tax",
    "tax avoidance scheme", "money laundering", "offshore account",
    "financial advice",
})


@dataclass(frozen=True)
class RiskAssessment:
    """What we found in a prompt."""

    triggered: bool
    categories: tuple[str, ...]
    patterns_matched: tuple[str, ...]

    def to_response(self) -> dict:
        return {
            "triggered": self.triggered,
            "categories": list(self.categories),
            "patterns_matched": list(self.patterns_matched),
        }


# --- Detector ---------------------------------------------------------------


def _scan_patterns(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    return [name for name, pat in patterns.items() if pat.search(text)]


def _scan_terms(text_lower: str, terms: Iterable[str]) -> bool:
    for term in terms:
        if term in text_lower:
            return True
    return False


def assess(prompt_text: str) -> RiskAssessment:
    """Return what categories of risk fire on this prompt."""
    if not prompt_text:
        return RiskAssessment(triggered=False, categories=(), patterns_matched=())

    text_lower = prompt_text.lower()
    matched_patterns = _scan_patterns(prompt_text, _PII_PATTERNS)

    categories: list[str] = []
    if matched_patterns:
        categories.append("pii")
    if _scan_terms(text_lower, _MEDICAL_TERMS):
        categories.append("medical")
    if _scan_terms(text_lower, _LEGAL_TERMS):
        categories.append("legal")
    if _scan_terms(text_lower, _FINANCIAL_TERMS):
        categories.append("financial")

    triggered = bool(categories)
    return RiskAssessment(
        triggered=triggered,
        categories=tuple(categories),
        patterns_matched=tuple(matched_patterns),
    )


def assess_messages(messages: list[dict]) -> RiskAssessment:
    """Scan every message content; combine results."""
    text = "\n".join(m.get("content", "") or "" for m in messages)
    return assess(text)
