"""Pytest fixtures shared across the test suite.

The autouse `_test_environment` fixture isolates tests from the production
routing config by pointing `settings.config_path` at `tests/test_config.yaml`
(mock-only routes) and resetting the dependency caches around every test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.api import deps
from app.core.settings import settings
from app.db import session as db_session

TEST_CONFIG = Path(__file__).resolve().parent / "test_config.yaml"
TEST_PRICING = Path(__file__).resolve().parent / "test_pricing.yaml"


def _reset_db_state() -> None:
    """Forget any cached engine/sessionmaker so the next init_engine builds fresh."""
    db_session._state["engine"] = None
    db_session._state["sessionmaker"] = None


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch, tmp_path):
    """Point settings at the test config + pricing + per-test sqlite file; clear caches."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "config_path", str(TEST_CONFIG))
    monkeypatch.setattr(settings, "pricing_path", str(TEST_PRICING))
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    _reset_db_state()
    deps.reset_caches()
    yield
    _reset_db_state()
    deps.reset_caches()


@pytest.fixture
def sample_config() -> dict:
    """Minimal valid routing config used by decision-engine tests."""
    return {
        "default": {"provider": "mock", "model": "mock-default-model"},
        "routing": {
            "rules": [
                {
                    "name": "rewrite_to_mock",
                    "when": {"task_type": "rewrite"},
                    "use": {"provider": "mock", "model": "mock-rewrite-model"},
                },
                {
                    "name": "summarize_to_mock",
                    "when": {"task_type": "summarization"},
                    "use": {"provider": "mock", "model": "mock-summary-model"},
                },
            ]
        },
    }
