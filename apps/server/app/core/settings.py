"""Typed application settings.

Loaded from environment variables and (optionally) a `.env` file at startup.
Exposed as a module-level `settings` singleton — every other module imports
that instance rather than re-instantiating `Settings()`.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor the .env path to apps/server/ so loading works regardless of the cwd
# uvicorn was launched from.
_SERVER_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _SERVER_DIR / ".env"


class Settings(BaseSettings):
    """Process-wide configuration. All fields are env-overridable."""

    app_name: str = "Switchboard"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    openai_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # Empty (default) = auth disabled. When set, clients must send
    # `Authorization: Bearer <api_token>` on /v1/* requests.
    api_token: str = ""

    # Resolved against the project root by `core/config.py` when relative.
    config_path: str = "configs/config.yaml"
    pricing_path: str = "configs/pricing.yaml"

    # Persistent request log. Relative sqlite paths are anchored to project root.
    database_url: str = "sqlite+aiosqlite:///data/router.db"

    # Opt-in: use the embeddings classifier as a fallback when keyword rules
    # don't match. Requires `pip install -e ".[embeddings]"`.
    enable_embedding_classifier: bool = False
    # Opt-in: semantic cache. Same embeddings dep applies.
    enable_semantic_cache: bool = False
    semantic_cache_similarity_threshold: float = 0.95

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
