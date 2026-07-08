"""Centralised logging setup using Rich for readable console output."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the root ``clipmaster`` logger once and return it."""
    global _CONFIGURED
    logger = logging.getLogger("clipmaster")
    if not _CONFIGURED:
        handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the ``clipmaster`` logger."""
    base = logging.getLogger("clipmaster")
    return base.getChild(name) if name else base
