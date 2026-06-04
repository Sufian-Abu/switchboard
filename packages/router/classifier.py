"""Task classifier.

Two strategies, used in order:

  1. **Keyword rules** — fast, deterministic. Match curated keyword sets per
     task_type. If any keyword appears in the joined message text, return that
     task type.
  2. **Embedding similarity** — only when keywords don't match and an embedder
     was supplied. Computes the cosine similarity between the input and a set
     of labeled prototype examples; returns the closest if it crosses the
     `min_similarity` threshold.

If neither path produces a hit, the classifier returns `general_chat`. The
embedder dependency is optional — the keyword-only path is fully self-contained.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from router.embedder import Embedder
from router.schemas import ClassifiedTask

# Default in-code keyword rules. Override by loading
# `configs/classifier_keywords.yaml` and passing the result as `keyword_rules`
# to `TaskClassifier`. Each tuple is (task_type, keywords, reason).
DEFAULT_KEYWORD_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("rewrite",
     ("rewrite", "rephrase", "grammar", "fix this email"),
     "Detected rewrite/rephrase style keywords."),
    ("summarization",
     ("summarize", "summary", "short summary"),
     "Detected summarization keywords."),
    ("structured_extraction",
     ("json", "extract fields", "structured output"),
     "Detected structured output keywords."),
    ("reasoning",
     ("architecture", "design", "tradeoff", "reason deeply"),
     "Detected higher-complexity reasoning keywords."),
]

# Labeled prototypes for embedding fallback. A few examples per task type;
# enough to anchor the semantic space without bloat. Real deployments would
# load these from a config file.
DEFAULT_PROTOTYPES: dict[str, list[str]] = {
    "rewrite": [
        "Please make this email sound more professional.",
        "Can you fix the wording on this paragraph?",
        "Rewrite the following sentence so it's clearer.",
    ],
    "summarization": [
        "Give me the key points in two sentences.",
        "TL;DR this article please.",
        "Condense this into a paragraph.",
    ],
    "structured_extraction": [
        "Pull the name, email, and date from this text.",
        "Output the data as JSON with these fields.",
        "Extract every product mentioned and return a list.",
    ],
    "reasoning": [
        "Walk me through the design tradeoffs.",
        "Compare and contrast these two approaches.",
        "Reason through why this implementation fails.",
    ],
}


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Vectors should be unit-norm (which SentenceTransformer does)."""
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


@dataclass
class TaskClassifier:
    """Hybrid task classifier (keywords → embeddings)."""

    embedder: Embedder | None = None
    prototypes: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_PROTOTYPES))
    keyword_rules: list[tuple[str, tuple[str, ...], str]] = field(
        default_factory=lambda: list(DEFAULT_KEYWORD_RULES)
    )
    min_similarity: float = 0.45

    # Embedded prototypes cached after first use: { task_type: [vector, ...] }
    _proto_vectors: dict[str, list[list[float]]] | None = None

    def _keyword_classify(self, full_text: str) -> ClassifiedTask | None:
        text = full_text.lower()
        for task_type, keywords, reason in self.keyword_rules:
            if any(k in text for k in keywords):
                return ClassifiedTask(task_type=task_type, reason=reason)
        return None

    def _ensure_prototype_vectors(self) -> dict[str, list[list[float]]]:
        if self._proto_vectors is None:
            assert self.embedder is not None
            self._proto_vectors = {
                tt: self.embedder.embed_batch(samples)
                for tt, samples in self.prototypes.items()
            }
        return self._proto_vectors

    def _embedding_classify(self, full_text: str) -> ClassifiedTask | None:
        if self.embedder is None:
            return None
        proto_vectors = self._ensure_prototype_vectors()
        query = self.embedder.embed(full_text)

        best_task: str | None = None
        best_sim: float = -1.0
        for task_type, vectors in proto_vectors.items():
            for v in vectors:
                sim = _cosine(query, v)
                if sim > best_sim:
                    best_sim = sim
                    best_task = task_type

        if best_task is None or best_sim < self.min_similarity:
            return None
        return ClassifiedTask(
            task_type=best_task,
            reason=f"Embedding similarity match ({best_sim:.2f} ≥ {self.min_similarity}).",
        )

    def classify(self, messages: list[dict]) -> ClassifiedTask:
        full_text = " ".join(m.get("content", "") for m in messages)
        keyword_hit = self._keyword_classify(full_text)
        if keyword_hit is not None:
            return keyword_hit

        embed_hit = self._embedding_classify(full_text)
        if embed_hit is not None:
            return embed_hit

        return ClassifiedTask(
            task_type="general_chat",
            reason="No specialized pattern matched, using general chat.",
        )
