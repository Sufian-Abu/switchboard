"""YAML routing config loader.

Resolves `settings.config_path` to an absolute file (relative paths are
interpreted from the project root, not the cwd), parses it, and validates
the top-level shape so a malformed file is rejected at boot rather than at
request time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.settings import settings

# project root = parents[4] of this file:
#   apps/server/app/core/config.py
#   parents[0]=core, [1]=app, [2]=server, [3]=apps, [4]=<repo root>
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _resolve(path_str: str) -> Path:
    raw = Path(path_str)
    return raw if raw.is_absolute() else (PROJECT_ROOT / raw).resolve()


def load_yaml_config() -> dict[str, Any]:
    """Load and shallow-validate the routing config. Raises FileNotFoundError / ValueError."""
    config_path = _resolve(settings.config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping, got {type(data).__name__}")

    # Light structural checks: missing keys are tolerated (defaults apply downstream),
    # but wrong types should fail fast at boot.
    if "default" in data and not isinstance(data["default"], dict):
        raise ValueError("`default` must be a mapping")
    if "routing" in data and not isinstance(data["routing"], dict):
        raise ValueError("`routing` must be a mapping")
    rules = data.get("routing", {}).get("rules", [])
    if rules is not None and not isinstance(rules, list):
        raise ValueError("`routing.rules` must be a list")

    return data


def load_pricing_config() -> dict[str, Any]:
    """Load the per-model pricing table.

    Missing file is tolerated (returns empty dict) so the server can run
    without cost estimation if no pricing.yaml is provided.
    """
    pricing_path = _resolve(settings.pricing_path)
    if not pricing_path.exists():
        return {}

    with pricing_path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Pricing root must be a mapping, got {type(data).__name__}")
    return data
