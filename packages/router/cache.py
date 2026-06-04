"""In-memory semantic cache for chat completions.

Each (prompt, model) tuple gets embedded once and stored with its
ProviderResponse. On lookup, we embed the incoming prompt and return the
stored response if cosine similarity to any cached vector clears the
configured threshold AND the model matches.

This is a deliberately simple implementation:
  - in-process only (no Redis / sqlite-vss),
  - linear scan per lookup (fine for small N),
  - LRU eviction at a fixed size.

When N grows past ~1000 entries or latency matters, swap this for sqlite-vss
or a real vector store. The interface stays the same.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass

from router.embedder import Embedder
from router.schemas import ProviderResponse


@dataclass
class CacheEntry:
    prompt: str
    model: str
    vector: list[float]
    response: ProviderResponse


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    entries: int = 0
    evictions: int = 0


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


class SemanticCache:
    """Cosine-similarity cache. Thread-safe via a coarse lock (good enough for stdlib asyncio)."""

    def __init__(
        self,
        embedder: Embedder,
        similarity_threshold: float = 0.95,
        max_entries: int = 256,
    ) -> None:
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def _embed(self, prompt: str) -> list[float]:
        return self.embedder.embed(prompt)

    def lookup(self, prompt: str, model: str) -> ProviderResponse | None:
        """Return a cached response if similarity ≥ threshold AND same model. Else None."""
        query = self._embed(prompt)
        with self._lock:
            best: tuple[float, str | None] = (-1.0, None)
            for key, entry in self._entries.items():
                if entry.model != model:
                    continue
                sim = _cosine(query, entry.vector)
                if sim > best[0]:
                    best = (sim, key)
            if best[1] is not None and best[0] >= self.similarity_threshold:
                # Touch to make this entry MRU.
                self._entries.move_to_end(best[1])
                self.stats.hits += 1
                return self._entries[best[1]].response
            self.stats.misses += 1
            return None

    def store(self, prompt: str, model: str, response: ProviderResponse) -> None:
        vector = self._embed(prompt)
        key = f"{model}::{prompt[:200]}"
        with self._lock:
            self._entries[key] = CacheEntry(
                prompt=prompt, model=model, vector=vector, response=response
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1
            self.stats.entries = len(self._entries)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "hits": self.stats.hits,
                "misses": self.stats.misses,
                "entries": len(self._entries),
                "evictions": self.stats.evictions,
                "similarity_threshold": self.similarity_threshold,
                "max_entries": self.max_entries,
            }
