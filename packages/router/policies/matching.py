"""MatchingPolicy — decides whether a rule's `when:` clause matches a request.

Supported keys inside `when:`:
  - `task_type: <str>`           — match the classified task type.
  - `metadata.<field>: <value>`  — match a field in the request's metadata.

Multiple keys are ANDed. Unknown keys fail closed (typos surface immediately
rather than silently matching everything).
"""
from __future__ import annotations

from typing import Any


_METADATA_PREFIX = "metadata."


class MatchingPolicy:
    """Stateless. Tests each rule's `when:` against the incoming request shape."""

    def matches(
        self,
        when: dict[str, Any],
        task_type: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        meta = metadata or {}
        for key, expected in when.items():
            if key == "task_type":
                if task_type != expected:
                    return False
            elif isinstance(key, str) and key.startswith(_METADATA_PREFIX):
                field = key[len(_METADATA_PREFIX):]
                actual = meta.get(field)
                if actual != expected:
                    return False
            else:
                # Unknown condition key — fail closed. Typos surface as misses
                # instead of silent matches.
                return False
        return True
