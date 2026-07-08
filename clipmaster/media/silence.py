"""Audio extraction and silence detection via ffmpeg.

* :func:`extract_audio` produces a mono 16 kHz WAV, the ideal input for Whisper.
* :func:`detect_silence` parses ffmpeg's ``silencedetect`` filter output into
  :class:`SilenceSpan` objects, later used by both the analysis report and the
  cleanup feature.
"""

from __future__ import annotations

import re
from pathlib import Path

from clipmaster.media.ffmpeg import run_ffmpeg
from clipmaster.models import SilenceSpan

# silencedetect logs lines like:
#   [silencedetect @ ...] silence_start: 12.345
#   [silencedetect @ ...] silence_end: 15.678 | silence_duration: 3.333
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def extract_audio(
    source: str | Path,
    dest: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    sample_rate: int = 16000,
    start_s: float | None = None,
    duration_s: float | None = None,
) -> Path:
    """Extract mono PCM audio from ``source`` into ``dest`` (WAV)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = []
    if start_s is not None:
        args += ["-ss", f"{start_s:.3f}"]
    if duration_s is not None:
        args += ["-t", f"{duration_s:.3f}"]
    args += [
        "-i",
        str(source),
        "-vn",                    # drop video
        "-ac",
        "1",                      # mono
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    run_ffmpeg(ffmpeg_bin, args)
    return dest


def detect_silence(
    source: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    noise_db: float = -30.0,
    min_silence_seconds: float = 0.6,
) -> list[SilenceSpan]:
    """Return the silent spans in ``source`` using ffmpeg's silencedetect."""
    args = [
        "-i",
        str(source),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_seconds}",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(ffmpeg_bin, args)
    stderr = result.stderr or ""

    spans: list[SilenceSpan] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        if (match := _SILENCE_START_RE.search(line)) is not None:
            pending_start = max(0.0, float(match.group(1)))
        elif (match := _SILENCE_END_RE.search(line)) is not None:
            end = float(match.group(1))
            start = pending_start if pending_start is not None else 0.0
            if end > start:
                spans.append(SilenceSpan(start=start, end=end))
            pending_start = None
    return spans
