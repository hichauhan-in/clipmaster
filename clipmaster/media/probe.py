"""Probe an input file into a typed :class:`MediaInfo`."""

from __future__ import annotations

from pathlib import Path

from clipmaster.media.ffmpeg import run_ffprobe
from clipmaster.models import AudioStreamInfo, MediaInfo, VideoStreamInfo


def _parse_fps(rate: str | None) -> float | None:
    """Parse an ffprobe frame-rate string like ``30000/1001``."""
    if not rate or rate in {"0/0", "0"}:
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def probe_media(path: str | Path, ffprobe_bin: str = "ffprobe") -> MediaInfo:
    """Return a :class:`MediaInfo` describing ``path``.

    Raises :class:`FileNotFoundError` if the file is missing and
    :class:`clipmaster.media.ffmpeg.FFmpegError` if ffprobe fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    payload = run_ffprobe(
        ffprobe_bin,
        ["-show_format", "-show_streams", str(path)],
    )

    fmt = payload.get("format", {}) or {}
    streams = payload.get("streams", []) or []

    duration = 0.0
    try:
        duration = float(fmt.get("duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    video: VideoStreamInfo | None = None
    audios: list[AudioStreamInfo] = []

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video is None:
            video = VideoStreamInfo(
                codec=stream.get("codec_name"),
                width=_to_int(stream.get("width")),
                height=_to_int(stream.get("height")),
                fps=_parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                bitrate=_to_int(stream.get("bit_rate")),
            )
        elif codec_type == "audio":
            tags = stream.get("tags", {}) or {}
            audios.append(
                AudioStreamInfo(
                    codec=stream.get("codec_name"),
                    sample_rate=_to_int(stream.get("sample_rate")),
                    channels=_to_int(stream.get("channels")),
                    language=tags.get("language"),
                )
            )

    # Fall back to a video-stream duration if the container had none.
    if duration <= 0:
        for stream in streams:
            if stream.get("codec_type") == "video":
                try:
                    duration = float(stream.get("duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    duration = 0.0
                if duration > 0:
                    break

    return MediaInfo(
        path=str(path),
        container=(fmt.get("format_name") or "").split(",")[0] or None,
        duration_s=duration,
        size_bytes=_to_int(fmt.get("size")) or path.stat().st_size,
        video=video,
        audios=audios,
    )
