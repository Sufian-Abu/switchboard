"""Tests for the embeddings fallback path of TaskClassifier.

Uses a small deterministic fake embedder so the suite stays offline and fast
(no sentence-transformers model download required).
"""
from __future__ import annotations

from typing import Iterable

from router.classifier import TaskClassifier


class _StubEmbedder:
    """Deterministic toy embedder: maps keywords to fixed vectors per task type.

    Returns a 4-dim vector. Each axis represents one task type. Words push the
    vector toward their axis. Good enough to verify the wiring + cosine math.
    """

    def __init__(self) -> None:
        self.axes = {
            "rewrite": 0,
            "summarization": 1,
            "structured_extraction": 2,
            "reasoning": 3,
        }
        self.lexicon = {
            "professional": "rewrite", "wording": "rewrite", "clearer": "rewrite",
            "tldr": "summarization", "condense": "summarization", "keypoints": "summarization",
            "extract": "structured_extraction", "fields": "structured_extraction",
            "tradeoff": "reasoning", "compare": "reasoning", "contrast": "reasoning",
        }

    def _vec(self, text: str) -> list[float]:
        v = [0.0, 0.0, 0.0, 0.0]
        words = text.lower().replace(",", " ").replace(".", " ").split()
        for word in words:
            tt = self.lexicon.get(word)
            if tt is not None:
                v[self.axes[tt]] += 1.0
        norm = sum(x * x for x in v) ** 0.5
        if norm == 0:
            # No signal → return zero vector. Cosine with anything else = 0
            # so the classifier falls through to general_chat as it should.
            return [0.0, 0.0, 0.0, 0.0]
        return [x / norm for x in v]

    def embed(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def test_falls_back_to_embeddings_when_no_keyword_match() -> None:
    classifier = TaskClassifier(
        embedder=_StubEmbedder(),
        prototypes={
            "rewrite": ["make this more professional"],
            "summarization": ["tldr please condense"],
            "structured_extraction": ["extract these fields"],
            "reasoning": ["compare tradeoff contrast"],
        },
        min_similarity=0.3,
    )
    # "Compare" hits the embedder lexicon → reasoning axis. None of the
    # keyword rules' triggers ("tradeoff", "design", etc.) appear, so the
    # keyword path misses and the embedding path takes over.
    result = classifier.classify(
        [{"role": "user", "content": "Compare the available choices please."}]
    )
    assert result.task_type == "reasoning"
    assert "similarity" in result.reason.lower()


def test_keyword_match_short_circuits_embeddings() -> None:
    classifier = TaskClassifier(embedder=_StubEmbedder())
    result = classifier.classify([{"role": "user", "content": "please rewrite this email"}])
    assert result.task_type == "rewrite"
    assert "keyword" in result.reason.lower() or "rewrite" in result.reason.lower()


def test_low_similarity_falls_through_to_general_chat() -> None:
    classifier = TaskClassifier(
        embedder=_StubEmbedder(),
        min_similarity=0.99,  # too strict for the stub vectors
    )
    result = classifier.classify([{"role": "user", "content": "lorem ipsum foo bar"}])
    assert result.task_type == "general_chat"


def test_no_embedder_still_works() -> None:
    """Keyword-only classifier should still work when embedder is None."""
    classifier = TaskClassifier(embedder=None)
    result = classifier.classify([{"role": "user", "content": "hello there"}])
    assert result.task_type == "general_chat"
