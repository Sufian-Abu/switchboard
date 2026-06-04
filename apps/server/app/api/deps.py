"""Cached factories for app-wide singletons.

Config, classifier, decision engine, and provider instances are built once per
process (via `functools.lru_cache`) instead of on every request. Tests can
reset state by calling `reset_caches()`.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import load_pricing_config, load_yaml_config
from app.core.settings import settings
from router.cache import SemanticCache
from router.classifier import TaskClassifier
from router.costing import CostEngine
from router.decision_engine import DecisionEngine
from router.embedder import try_get_embedder
from router.errors import ConfigError
from router.providers.base import BaseProvider
from router.providers.gemini import GeminiProvider
from router.providers.groq import GroqProvider
from router.providers.mock_provider import MockProvider
from router.providers.ollama import OllamaProvider
from router.providers.openai_provider import OpenAIProvider


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Load and cache routing config from YAML. Raises ConfigError on failure."""
    try:
        return load_yaml_config()
    except (FileNotFoundError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


@lru_cache(maxsize=1)
def get_classifier() -> TaskClassifier:
    # Try to attach an embedder; falls back to keyword-only if the
    # optional `embeddings` extra wasn't installed.
    embedder = try_get_embedder() if settings.enable_embedding_classifier else None
    return TaskClassifier(embedder=embedder)


@lru_cache(maxsize=1)
def get_decision_engine() -> DecisionEngine:
    return DecisionEngine(config=get_config(), cost_engine=get_cost_engine())


@lru_cache(maxsize=1)
def get_cost_engine() -> CostEngine:
    """Load pricing table and return a cached CostEngine."""
    try:
        pricing = load_pricing_config()
    except (FileNotFoundError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc
    return CostEngine(pricing=pricing)


@lru_cache(maxsize=1)
def get_semantic_cache() -> SemanticCache | None:
    """Returns a SemanticCache, or None when disabled / embeddings extra missing."""
    if not settings.enable_semantic_cache:
        return None
    embedder = try_get_embedder()
    if embedder is None:
        # Embeddings extra not installed → cache silently disabled.
        return None
    return SemanticCache(
        embedder=embedder,
        similarity_threshold=settings.semantic_cache_similarity_threshold,
    )


@lru_cache(maxsize=None)
def get_provider(provider_name: str) -> BaseProvider:
    """Return a cached provider instance by name. Raises ConfigError if unknown."""
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url)
    if provider_name == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key)
    if provider_name == "groq":
        return GroqProvider(api_key=settings.groq_api_key)
    if provider_name == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key)
    raise ConfigError(f"Unsupported provider: {provider_name}")


def reset_caches() -> None:
    """Clear all factory caches. Used by tests to force re-construction.

    Defensive: tests sometimes monkeypatch these names to plain functions
    that don't have `cache_clear`. Skip those rather than fail teardown.
    """
    for fn in (
        get_config,
        get_classifier,
        get_decision_engine,
        get_cost_engine,
        get_semantic_cache,
        get_provider,
    ):
        cache_clear = getattr(fn, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()
