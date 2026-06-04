"""Tests for: Prometheus /metrics endpoint, loadable keyword rules YAML."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.settings import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --- Prometheus /metrics ----------------------------------------------------


def test_metrics_endpoint_returns_200(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    # Either real prometheus output OR the graceful-degrade placeholder.
    assert r.headers["content-type"].startswith("text/plain")


def test_metrics_endpoint_increments_after_traffic(client: TestClient) -> None:
    """A successful chat completion should bump switchboard_requests_total."""
    from app.metrics import is_enabled

    if not is_enabled():
        pytest.skip("prometheus-client not installed in this env")

    # Generate traffic.
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "please rewrite this email"}]},
    )
    assert r.status_code == 200

    metrics_body = client.get("/metrics").text
    assert "switchboard_requests_total" in metrics_body
    # The counter for /v1/chat/completions:succeeded should appear and be > 0.
    assert 'switchboard_requests_total{endpoint="/v1/chat/completions",status="succeeded"}' in metrics_body


# --- Loadable keyword rules -------------------------------------------------


def test_classifier_loads_keywords_from_yaml(monkeypatch, tmp_path) -> None:
    kw_file = tmp_path / "keywords.yaml"
    kw_file.write_text(
        "- task_type: rewrite\n"
        "  keywords: ['polish', 'tidy up']\n"
        "  reason: 'Detected custom rewrite keywords.'\n"
    )
    monkeypatch.setattr(settings, "classifier_keywords_path", str(kw_file))
    deps.reset_caches()

    classifier = deps.get_classifier()
    assert classifier.keyword_rules == [
        ("rewrite", ("polish", "tidy up"), "Detected custom rewrite keywords.")
    ]

    # The custom keyword should hit; an old default keyword should NOT.
    res_custom = classifier.classify([{"role": "user", "content": "please polish this paragraph"}])
    assert res_custom.task_type == "rewrite"
    assert "custom rewrite keywords" in res_custom.reason

    res_default = classifier.classify([{"role": "user", "content": "please rewrite this email"}])
    # "rewrite" no longer matches the (overridden) keyword list — falls through.
    assert res_default.task_type == "general_chat"


def test_invalid_keywords_yaml_raises_config_error(monkeypatch, tmp_path) -> None:
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("not_a_list\n")
    monkeypatch.setattr(settings, "classifier_keywords_path", str(bad_file))
    deps.reset_caches()

    from router.errors import ConfigError

    with pytest.raises(ConfigError):
        deps.get_classifier()


def test_keyword_rule_missing_field_raises(monkeypatch, tmp_path) -> None:
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text(
        "- task_type: rewrite\n"
        "  # missing keywords\n"
        "  reason: 'No keywords field'\n"
    )
    monkeypatch.setattr(settings, "classifier_keywords_path", str(bad_file))
    deps.reset_caches()

    from router.errors import ConfigError

    with pytest.raises(ConfigError):
        deps.get_classifier()
