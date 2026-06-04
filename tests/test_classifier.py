"""Unit tests for the keyword-rule TaskClassifier."""
from __future__ import annotations

import pytest

from router.classifier import TaskClassifier


@pytest.fixture
def classifier() -> TaskClassifier:
    return TaskClassifier()


@pytest.mark.parametrize(
    "content,expected_type",
    [
        ("Please rewrite this email", "rewrite"),
        ("Fix this email for me", "rewrite"),
        ("Check the grammar of this paragraph", "rewrite"),
        ("Give me a short summary of this", "summarization"),
        ("Summarize the article please", "summarization"),
        ("Return JSON with the user details", "structured_extraction"),
        ("Extract fields from this resume", "structured_extraction"),
        ("Discuss the architecture tradeoffs", "reasoning"),
        ("Reason deeply about this design", "reasoning"),
        ("Hello, how are you today?", "general_chat"),
        ("", "general_chat"),
    ],
)
def test_classify_branches(classifier: TaskClassifier, content: str, expected_type: str) -> None:
    result = classifier.classify([{"role": "user", "content": content}])
    assert result.task_type == expected_type
    assert result.reason  # non-empty reason for every branch


def test_classify_first_match_wins(classifier: TaskClassifier) -> None:
    """When multiple rules could match, the first rule (rewrite) wins."""
    result = classifier.classify(
        [{"role": "user", "content": "rewrite and summarize this"}]
    )
    assert result.task_type == "rewrite"


def test_classify_handles_multiple_messages(classifier: TaskClassifier) -> None:
    """Classifier scans all messages, not just the last (system + user joined)."""
    result = classifier.classify(
        [
            {"role": "system", "content": "Provide a short summary."},
            {"role": "user", "content": "Here is the text."},
        ]
    )
    assert result.task_type == "summarization"
