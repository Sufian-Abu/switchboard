"""Shared embedding service for classifier + semantic cache.

Wraps `sentence-transformers` behind an `Embedder` protocol so both consumers
(classifier and semantic cache) can use the same vectors. The dependency is
optional — code paths that need embeddings should call `try_get_embedder()`
and degrade gracefully when None is returned.

Why not require sentence-transformers? It pulls in ~600MB of torch on first
install. Users who only want the keyword classifier + no semantic cache
shouldn't pay that cost.
"""
from __future__ import annotations

from typing import Iterable, Protocol


class Embedder(Protocol):
    """Anything that turns text into a unit-norm vector."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Wraps a sentence-transformers model. Model is loaded lazily on first use."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None  # type: ignore[assignment]

    def _ensure_loaded(self):
        if self._model is None:
            # Imported lazily so projects that don't use embeddings don't pay
            # the import cost (or fail at module load if the extra isn't installed).
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        model = self._ensure_loaded()
        vectors = model.encode(list(texts), normalize_embeddings=True)
        return [v.tolist() for v in vectors]


def try_get_embedder(model_name: str | None = None) -> Embedder | None:
    """Return a usable Embedder, or None if the optional dep isn't installed.

    Doesn't actually load the model — just verifies the import works so the
    caller can decide whether to fall back to keyword-only behavior.
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return None
    return SentenceTransformerEmbedder(
        model_name=model_name or "sentence-transformers/all-MiniLM-L6-v2"
    )
