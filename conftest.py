"""Root conftest: makes `router` (under packages/) and `app` (under apps/server/) importable.

The repo uses a multi-root layout that does not match standard pyproject discovery,
so we wire up sys.path here for pytest. This file is automatically loaded by pytest
when collecting from the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for entry in (ROOT / "packages", ROOT / "apps" / "server"):
    path = str(entry)
    if path not in sys.path:
        sys.path.insert(0, path)
