"""Shared ffmpeg building blocks for the render actions (cleanup + shorts).

Kept separate so cleanup and shorts share exactly one implementation of the
encode settings, the keep-span trim/concat graph and the vertical reframe.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Sequence

from clipmaster.config import RenderConfig
from clipmaster.media.ffmpeg import run_ffmpeg_progress

_SLUG_RE = re.compile(r"[^a-z0-9]+")

ProgressFn = Callable[[float], None]  # receives a 0..1 fraction


def slugify(name: str, *, fallback: str = "clip", max_len: int = 48) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or fallback


def encode_args(render: RenderConfig, *, has_audio: bool) -> list[str]:
    """The common video/audio encoder options for a rendered mp4."""
    args = [
        "-c:v",
        render.video_codec,
        "-preset",
        render.preset,
        "-crf",
        str(render.crf),
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio:
        args += ["-c:a", render.audio_codec, "-b:a", render.audio_bitrate, "-ac", "2"]
    else:
        args += ["-an"]
    return args


def _clamp_fraction(elapsed: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, elapsed / total))


def keep_and_concat(
    source: Path,
    spans: Sequence[tuple[float, float]],
    dest: Path,
    *,
    has_audio: bool,
    render: RenderConfig,
    ffmpeg_bin: str = "ffmpeg",
    on_progress: ProgressFn | None = None,
) -> Path:
    """Re-encode ``source`` keeping only ``spans`` (concatenated) into ``dest``.

    A single-pass ``filter_complex`` trims each span and concatenates them, so the
    output has clean, gap-free timestamps and correct A/V sync regardless of the
    source keyframe layout. The graph is written to a script file (via
    ``-filter_complex_script``) so an arbitrary number of spans never hits the
    OS command-line length limit.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = sum(max(0.0, e - s) for s, e in spans)

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, (start, end) in enumerate(spans):
        parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}];"
        )
        concat_inputs.append(f"[v{i}]")
        if has_audio:
            parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}];"
            )
            concat_inputs.append(f"[a{i}]")
    n = len(spans)
    if has_audio:
        parts.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=1[outv][outa]")
    else:
        parts.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=0[outv]")
    graph = "\n".join(parts)

    script_path = dest.parent / f"{dest.stem}.filter.txt"
    script_path.write_text(graph, encoding="utf-8")

    args = ["-i", str(source), "-filter_complex_script", str(script_path), "-map", "[outv]"]
    if has_audio:
        args += ["-map", "[outa]"]
    args += encode_args(render, has_audio=has_audio)
    args += ["-movflags", "+faststart", str(dest)]

    def _cb(elapsed: float) -> None:
        if on_progress:
            on_progress(_clamp_fraction(elapsed, total))

    run_ffmpeg_progress(ffmpeg_bin, args, on_progress=_cb)
    script_path.unlink(missing_ok=True)
    return dest


def _vertical_graph(render: RenderConfig) -> str:
    w, h = render.shorts_width, render.shorts_height
    if render.shorts_blur_background:
        return (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=20:2[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[outv]"
        )
    return (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[outv]"
    )


def render_vertical_short(
    source: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    has_audio: bool,
    render: RenderConfig,
    ffmpeg_bin: str = "ffmpeg",
    on_progress: ProgressFn | None = None,
) -> Path:
    """Render ``[start, end]`` of ``source`` as a vertical 9:16 short into ``dest``.

    The source frame is letterboxed (never cropped) over a blurred fill of itself
    — the familiar reel look — so slides, code and diagrams stay fully visible.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end - start)
    graph = _vertical_graph(render)

    args = [
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-filter_complex",
        graph,
        "-map",
        "[outv]",
    ]
    if has_audio:
        args += ["-map", "0:a?"]
    args += encode_args(render, has_audio=has_audio)
    args += ["-movflags", "+faststart", str(dest)]

    def _cb(elapsed: float) -> None:
        if on_progress:
            on_progress(_clamp_fraction(elapsed, duration))

    run_ffmpeg_progress(ffmpeg_bin, args, on_progress=_cb)
    return dest
