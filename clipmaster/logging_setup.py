"""Centralised logging setup using Rich for readable console output.

Beyond the console, this module installs two extra sinks used by the desktop
app's Diagnostics tab:

* an in-memory **ring buffer** so ``/api/logs`` can return the most recent lines
  without re-reading a file, and
* an optional **rotating file handler** that persists logs to a user-chosen path.
"""

from __future__ import annotations

import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False

# Format shared by the ring buffer and the on-disk log file.
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# When a directory (not a *.log file) is chosen, logs land in this file inside it.
DEFAULT_LOG_FILENAME = "clipmaster.log"


class RingBufferHandler(logging.Handler):
    """Keeps the most recent formatted log records in memory for the API."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            self.buffer.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)

    def lines(self, limit: int | None = None) -> list[str]:
        items = list(self.buffer)
        return items[-limit:] if limit else items


_ring_handler: RingBufferHandler | None = None
_file_handler: RotatingFileHandler | None = None
_log_file_path: Path | None = None


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    """Configure the root ``clipmaster`` logger once and return it."""
    global _CONFIGURED, _ring_handler
    logger = logging.getLogger("clipmaster")
    if not _CONFIGURED:
        console = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
        console.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(console)

        _ring_handler = RingBufferHandler()
        _ring_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(_ring_handler)

        logger.propagate = False
        _CONFIGURED = True

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if log_file:
        try:
            configure_file_logging(log_file)
        except OSError:  # pragma: no cover - env dependent
            logger.warning("Could not enable file logging at %s", log_file)
    return logger


def _resolve_log_file(path: str | Path) -> Path:
    """Interpret *path* as either a log file (``*.log``) or a directory."""
    resolved = Path(path).expanduser()
    if resolved.suffix.lower() != ".log":
        resolved = resolved / DEFAULT_LOG_FILENAME
    return resolved


def configure_file_logging(path: str | Path) -> Path:
    """(Re)direct file logging to *path*; returns the resolved log file path."""
    global _file_handler, _log_file_path
    logger = logging.getLogger("clipmaster")
    target = _resolve_log_file(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if _file_handler is not None:
        logger.removeHandler(_file_handler)
        _file_handler.close()

    handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(handler)
    _file_handler = handler
    _log_file_path = target
    logger.info("File logging enabled at %s", target)
    return target


def current_log_file() -> Path | None:
    """Absolute path of the active log file, or ``None`` if not enabled."""
    return _log_file_path


def recent_log_lines(limit: int = 200) -> list[str]:
    """Return the most recent captured log lines (newest last)."""
    if _ring_handler is None:
        return []
    return _ring_handler.lines(limit)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the ``clipmaster`` logger."""
    base = logging.getLogger("clipmaster")
    return base.getChild(name) if name else base
