"""Logging helper.

Returns a module-scoped logger that prints `[timestamp] LEVEL name - msg` to
stderr. Each call is idempotent: re-getting the same name does not add another
handler, so test runs and reloads stay clean.
"""
from __future__ import annotations

import logging


def get_logger(name: str = "llm-router") -> logging.Logger:
    """Return a configured logger with a single stream handler."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    return logger
