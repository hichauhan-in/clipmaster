"""Thin, well-behaved wrappers around the ffmpeg / ffprobe executables.

We shell out to the binaries (rather than binding a library) because that is the
most portable option and matches how the target machine already has ffmpeg
installed. All calls are synchronous and raise :class:`FFmpegError` on failure so
callers can convert them into user-facing pipeline errors.
"""

from __future__ import annotations

import json
import subprocess
from typing import Sequence

from clipmaster.logging_setup import get_logger

logger = get_logger("media.ffmpeg")


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation exits non-zero."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr
        preview = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        super().__init__(
            f"Command failed ({returncode}): {' '.join(command[:2])} ... -> {preview}"
        )


def _run(command: Sequence[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    logger.debug("exec: %s", " ".join(command))
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=capture,
            text=True,
        )
    except FileNotFoundError as exc:  # binary missing on PATH
        raise FFmpegError(command, 127, str(exc)) from exc
    if result.returncode != 0:
        raise FFmpegError(command, result.returncode, result.stderr or "")
    return result


def run_ffprobe(ffprobe_bin: str, args: Sequence[str]) -> dict:
    """Run ffprobe with JSON output and return the parsed payload."""
    command = [ffprobe_bin, "-v", "error", "-print_format", "json", *args]
    result = _run(command)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(command, 0, f"invalid ffprobe JSON: {exc}") from exc


def run_ffmpeg(ffmpeg_bin: str, args: Sequence[str]) -> subprocess.CompletedProcess:
    """Run ffmpeg with ``-y`` (overwrite) and no banner noise.

    ``ffmpeg`` writes its progress and diagnostics to stderr even on success, so
    the returned ``CompletedProcess.stderr`` is useful (e.g. silencedetect lines).
    """
    command = [ffmpeg_bin, "-hide_banner", "-nostdin", "-y", *args]
    return _run(command)
