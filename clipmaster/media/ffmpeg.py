"""Thin, well-behaved wrappers around the ffmpeg / ffprobe executables.

We shell out to the binaries (rather than binding a library) because that is the
most portable option and matches how the target machine already has ffmpeg
installed. All calls are synchronous and raise :class:`FFmpegError` on failure so
callers can convert them into user-facing pipeline errors.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from typing import Callable, Sequence

from clipmaster.logging_setup import get_logger

logger = get_logger("media.ffmpeg")

_FFMPEG_MAJOR_CACHE: dict[str, int | None] = {}


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


def ffmpeg_major_version(ffmpeg_bin: str) -> int | None:
    """Return the ffmpeg major version (e.g. ``6`` or ``7``), or ``None`` if unknown.

    Cached per binary. Used to select between options whose spelling changed across
    releases — notably ``-filter_complex_script``, which ffmpeg 7.0 removed in favour
    of the generic ``-/filter_complex <file>`` file-read syntax.
    """
    if ffmpeg_bin in _FFMPEG_MAJOR_CACHE:
        return _FFMPEG_MAJOR_CACHE[ffmpeg_bin]
    version: int | None = None
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-version"],
            check=False,
            capture_output=True,
            text=True,
        )
        first = (result.stdout or "").splitlines()[0] if result.stdout else ""
        match = re.search(r"version\s+n?(\d+)\.", first)
        if match:
            version = int(match.group(1))
    except (OSError, ValueError):
        version = None
    _FFMPEG_MAJOR_CACHE[ffmpeg_bin] = version
    return version


def run_ffmpeg(ffmpeg_bin: str, args: Sequence[str]) -> subprocess.CompletedProcess:
    """Run ffmpeg with ``-y`` (overwrite) and no banner noise.

    ``ffmpeg`` writes its progress and diagnostics to stderr even on success, so
    the returned ``CompletedProcess.stderr`` is useful (e.g. silencedetect lines).
    """
    command = [ffmpeg_bin, "-hide_banner", "-nostdin", "-y", *args]
    return _run(command)


def _parse_out_time(line: str) -> float | None:
    """Parse an ffmpeg ``-progress`` line into elapsed output seconds, if present."""
    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
        # ``out_time_ms`` is historically microseconds in ffmpeg; both are µs.
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        value = line.split("=", 1)[1].strip()
        if value in ("", "N/A"):
            return None
        try:
            hh, mm, ss = value.split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        except ValueError:
            return None
    return None


def run_ffmpeg_progress(
    ffmpeg_bin: str,
    args: Sequence[str],
    *,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Run ffmpeg, streaming ``-progress`` output to ``on_progress(elapsed_s)``.

    Unlike :func:`run_ffmpeg` this does not buffer until completion; it reads the
    machine-readable progress stream line by line so a long re-encode can report a
    live percentage. Raises :class:`FFmpegError` on a non-zero exit.

    ``stderr`` is captured to a temp file (not a pipe) so a chatty encode can never
    dead-lock on a full pipe buffer while we are busy reading stdout progress.
    """
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        *args,
    ]
    logger.debug("exec (progress): %s", " ".join(command))
    with tempfile.TemporaryFile(mode="w+") as errfile:
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=errfile,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:  # binary missing on PATH
            raise FFmpegError(command, 127, str(exc)) from exc

        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                elapsed = _parse_out_time(raw.strip())
                if elapsed is not None and on_progress is not None:
                    on_progress(elapsed)
        finally:
            proc.stdout.close()
        returncode = proc.wait()
        if returncode != 0:
            errfile.seek(0)
            raise FFmpegError(command, returncode, errfile.read())
