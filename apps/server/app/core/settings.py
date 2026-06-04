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

    # Extend the bearer-token check to /dashboard/* and /v1/cache/stats.
    # Browsers can authenticate with HTTP Basic auth (any username, password = api_token).
    # Leave False for local dev; set True for any deployment that isn't behind a reverse proxy
    # which already locks down those paths.
    dashboard_auth: bool = False

    # Hard cap on the day's recorded spend in USD. 0 disables.
    # When today's `request_log.estimated_usd` sum exceeds this, /v1/chat/{completions,compare}
    # returns 503 with a structured error until the next UTC day.
    max_daily_usd: float = 0.0

    # When False (default), client requests carrying a `model:` field are rejected with 400.
    # Set True to keep the prior behaviour (route them through the mock provider so they
    # don't hit a real upstream by accident).
    allow_client_model_override: bool = False

    # Resolved against the project root by `core/config.py` when relative.
    config_path: str = "configs/config.yaml"
    pricing_path: str = "configs/pricing.yaml"

    # Persistent request log. Relative sqlite paths are anchored to project root.
    database_url: str = "sqlite+aiosqlite:///data/router.db"

    # Opt-in: use the embeddings classifier as a fallback when keyword rules
    # don't match. Requires `pip install -e ".[embeddings]"`.
    enable_embedding_classifier: bool = False
    classifier_keywords_path: str = "configs/classifier_keywords.yaml"
    classifier_prototypes_path: str = "configs/classifier_prototypes.yaml"

    # Opt-in: semantic cache. Same embeddings dep applies. Threshold is 0.97 because
    # 0.95 has been seen to merge prompts that diverge in intent (negation, named
    # entities, formatting). Tighten further or use `cache_strict: true` on the rule
    # for sensitive workloads. NEVER enable for legal/medical/PII without per-tenant
    # cache scoping (currently not built).
    enable_semantic_cache: bool = False
    semantic_cache_similarity_threshold: float = 0.97

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
