"""Shared ffmpeg building blocks for the render actions (cleanup + shorts).

Kept separate so cleanup and shorts share exactly one implementation of the
encode settings, the keep-span trim/concat graph and the vertical reframe.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Sequence

from clipmaster.config import RenderConfig
from clipmaster.media.ffmpeg import ffmpeg_major_version, run_ffmpeg_progress

_SLUG_RE = re.compile(r"[^a-z0-9]+")

ProgressFn = Callable[[float], None]  # receives a 0..1 fraction


def slugify(name: str, *, fallback: str = "clip", max_len: int = 48) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or fallback


def _filter_complex_args(
    graph: str, dest: Path, ffmpeg_bin: str
) -> tuple[list[str], Path | None]:
    """Build the ffmpeg args for a large complex filtergraph.

    ffmpeg 7.0 removed ``-filter_complex_script``; the generic replacement is the
    ``-/filter_complex <file>`` file-read syntax (a ``/`` before the option name
    tells ffmpeg to read the value from a file). Older ffmpeg predates that syntax,
    so we choose based on the detected major version. Writing the graph to a file
    also keeps us clear of the OS command-line length limit when there are many
    spans. When the version can't be detected we fall back to inlining the graph,
    which works on every ffmpeg release.

    Returns the option args plus the temp script path to clean up (or ``None`` when
    the graph was inlined).
    """
    major = ffmpeg_major_version(ffmpeg_bin)
    if major is None:
        return ["-filter_complex", graph], None
    script_path = dest.parent / f"{dest.stem}.filter.txt"
    script_path.write_text(graph, encoding="utf-8")
    if major >= 7:
        return ["-/filter_complex", str(script_path)], script_path
    return ["-filter_complex_script", str(script_path)], script_path


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
    source keyframe layout. The graph is written to a script file and passed with
    the version-appropriate option (see :func:`_filter_complex_args`) so an
    arbitrary number of spans never hits the OS command-line length limit.
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

    fc_args, script_path = _filter_complex_args(graph, dest, ffmpeg_bin)

    args = ["-i", str(source), *fc_args, "-map", "[outv]"]
    if has_audio:
        args += ["-map", "[outa]"]
    args += encode_args(render, has_audio=has_audio)
    args += ["-movflags", "+faststart", str(dest)]

    def _cb(elapsed: float) -> None:
        if on_progress:
            on_progress(_clamp_fraction(elapsed, total))

    run_ffmpeg_progress(ffmpeg_bin, args, on_progress=_cb)
    if script_path is not None:
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
