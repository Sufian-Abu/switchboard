"""Unit tests for the SemanticCache."""
from __future__ import annotations

from router.cache import SemanticCache
from router.schemas import ProviderResponse


class _ToyEmbedder:
    """Deterministic embedder: maps each unique word to a fresh axis (very small lexicon)."""

    def __init__(self) -> None:
        self.axes: dict[str, int] = {}

    def _vec(self, text: str) -> list[float]:
        words = text.lower().split()
        v = [0.0] * 32  # generous
        for w in words:
            idx = self.axes.setdefault(w, len(self.axes) % 32)
            v[idx] += 1.0
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v] if norm > 0 else v

    def embed(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_batch(self, texts):
        return [self._vec(t) for t in texts]


def _make_response(content: str) -> ProviderResponse:
    return ProviderResponse(content=content, prompt_tokens=10, completion_tokens=5)


def test_miss_when_empty() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder())
    assert cache.lookup("hello world", "m1") is None
    assert cache.stats.misses == 1
    assert cache.stats.hits == 0


def test_hit_on_exact_match() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder(), similarity_threshold=0.95)
    cache.store("rewrite this email politely", "m1", _make_response("polite version"))
    hit = cache.lookup("rewrite this email politely", "m1")
    assert hit is not None
    assert hit.content == "polite version"
    assert cache.stats.hits == 1


def test_miss_on_different_model() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder(), similarity_threshold=0.5)
    cache.store("hello world", "m1", _make_response("cached"))
    assert cache.lookup("hello world", "m2") is None


def test_miss_below_threshold() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder(), similarity_threshold=0.95)
    cache.store("rewrite this email politely", "m1", _make_response("cached"))
    # Almost no overlap — different words → low similarity.
    assert cache.lookup("explain quantum mechanics", "m1") is None
    assert cache.stats.misses == 1


def test_lru_eviction() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder(), max_entries=2)
    cache.store("a a a", "m", _make_response("first"))
    cache.store("b b b", "m", _make_response("second"))
    cache.store("c c c", "m", _make_response("third"))  # evicts "a a a"
    assert cache.stats.entries == 2
    assert cache.stats.evictions == 1


def test_snapshot_has_fields() -> None:
    cache = SemanticCache(embedder=_ToyEmbedder(), similarity_threshold=0.9, max_entries=10)
    snap = cache.snapshot()
    for key in ("hits", "misses", "entries", "evictions", "similarity_threshold", "max_entries"):
        assert key in snap
