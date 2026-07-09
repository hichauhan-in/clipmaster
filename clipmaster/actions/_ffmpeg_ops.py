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
    fade_seconds: float = 0.0,
    fade_min_gap: float = 5.0,
) -> Path:
    """Re-encode ``source`` keeping only ``spans`` (concatenated) into ``dest``.

    A single-pass ``filter_complex`` trims each span and concatenates them, so the
    output has clean, gap-free timestamps and correct A/V sync regardless of the
    source keyframe layout. The graph is written to a script file and passed with
    the version-appropriate option (see :func:`_filter_complex_args`) so an
    arbitrary number of spans never hits the OS command-line length limit.

    When ``fade_seconds`` > 0 the cut is smoothed: it fades in on the opening
    frame, and wherever a large stretch (``fade_min_gap`` seconds or more) was
    removed between two kept spans it fades the outgoing span down and the
    incoming span up, so a big jump reads as an intentional transition rather
    than a glitch.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = sum(max(0.0, e - s) for s, e in spans)
    n = len(spans)

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, (start, end) in enumerate(spans):
        dur = max(0.0, end - start)
        gap_before = start - spans[i - 1][1] if i > 0 else None
        gap_after = spans[i + 1][0] - end if i < n - 1 else None
        # Fade in on the very first frame and coming out of a big removed gap;
        # fade out going into a big removed gap (never at the natural ending).
        fade_in = fade_seconds > 0 and (i == 0 or (gap_before or 0.0) >= fade_min_gap)
        fade_out = fade_seconds > 0 and gap_after is not None and gap_after >= fade_min_gap
        f = min(fade_seconds, dur / 2)
        if f <= 0.01:
            fade_in = fade_out = False

        vchain = f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS"
        if fade_in:
            vchain += f",fade=t=in:st=0:d={f:.3f}"
        if fade_out:
            vchain += f",fade=t=out:st={dur - f:.3f}:d={f:.3f}"
        parts.append(f"{vchain}[v{i}];")
        concat_inputs.append(f"[v{i}]")

        if has_audio:
            # aresample=async=1 keeps audio locked to the reset timeline so the
            # cut does not drift out of sync with the video across many joins.
            achain = (
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
                "asetpts=PTS-STARTPTS,aresample=async=1"
            )
            if fade_in:
                achain += f",afade=t=in:st=0:d={f:.3f}"
            if fade_out:
                achain += f",afade=t=out:st={dur - f:.3f}:d={f:.3f}"
            parts.append(f"{achain}[a{i}];")
            concat_inputs.append(f"[a{i}]")

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


def _fit_graph(w: int, h: int, *, blur: bool) -> str:
    """Whole frame fitted into a ``w``×``h`` canvas — over a blurred fill of
    itself (``blur``) or letterboxed on black."""
    if blur:
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


def _round_alpha_expr(radius: int) -> str:
    """A ``geq`` alpha expression that is opaque except outside the four rounded
    corners of the layer it is applied to (uses the layer's own W/H)."""
    r = max(0, int(radius))
    return (
        f"if(gt(abs(X-(W-1)/2),(W-1)/2-{r})*gt(abs(Y-(H-1)/2),(H-1)/2-{r}),"
        f"if(lte(hypot(abs(X-(W-1)/2)-((W-1)/2-{r}),abs(Y-(H-1)/2)-((H-1)/2-{r})),{r}),255,0),"
        f"255)"
    )


def _rounded_card_chain(
    label_in: str, wc: int, hc: int, radius: int, card_ar: float, label_out: str
) -> str:
    """Centre-crop ``label_in`` to the card aspect, scale to ``wc``×``hc`` and
    round the corners, producing ``label_out`` (a yuva420p rounded layer). A
    ``card_ar`` of 1.0 is a square crop; 16/9 keeps a horizontal frame intact."""
    return (
        f"[{label_in}]crop='min(iw,ih*{card_ar:.6f})':'min(ih,iw/{card_ar:.6f})',"
        f"scale={wc}:{hc},setsar=1,format=yuva420p,"
        f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='{_round_alpha_expr(radius)}'[{label_out}]"
    )


def _card_graph(
    render: RenderConfig, *, duration: float, background: str, w: int, h: int, card_ar: float
) -> str:
    """Short with the source as a rounded card centred on a ``w``×``h`` canvas,
    leaving a visible margin all around. The card aspect is ``card_ar`` (1:1 for
    a vertical canvas, 16:9 for a horizontal one so nothing is cropped away).

    ``background`` is either ``"blur"`` — a zoomed, blurred copy of the same
    frame behind the card — or ``"black"`` — a solid-black canvas with a thin
    white border ring tracing the rounded card.
    """
    sm = max(0, int(render.shorts_card_side_margin))
    avail_w = max(2, w - 2 * sm)
    avail_h = max(2, h - 2 * sm)
    # Largest card of aspect card_ar that fits inside the margins on both axes.
    wc = avail_w
    hc = int(round(wc / card_ar))
    if hc > avail_h:
        hc = avail_h
        wc = int(round(hc * card_ar))
    wc = max(2, wc - wc % 2)
    hc = max(2, hc - hc % 2)
    r = max(0, min(int(render.shorts_card_radius), wc // 2, hc // 2))
    vx = (w - wc) // 2
    oy = (h - hc) // 2  # centred → equal margins around the card

    if background == "black":
        b = max(0, int(render.shorts_card_border))
        dur_s = f"{max(0.1, duration):.3f}"
        fg = _rounded_card_chain("0:v", wc, hc, r, card_ar, "fg")
        if b > 0:
            wbc, hbc = wc + 2 * b, hc + 2 * b
            return (
                f"{fg};"
                f"color=c=black:s={w}x{h}:d={dur_s}[bg];"
                f"color=c=white:s={wbc}x{hbc}:d={dur_s},format=yuva420p,"
                f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
                f"a='{_round_alpha_expr(r + b)}'[bd];"
                f"[bg][bd]overlay={vx - b}:{oy - b}[bg2];"
                f"[bg2][fg]overlay={vx}:{oy},setsar=1[outv]"
            )
        return (
            f"{fg};"
            f"color=c=black:s={w}x{h}:d={dur_s}[bg];"
            f"[bg][fg]overlay={vx}:{oy},setsar=1[outv]"
        )

    # Blurred background (default): a zoom-filled, blurred copy of the frame.
    fg = _rounded_card_chain("src", wc, hc, r, card_ar, "fg")
    return (
        f"[0:v]split=2[src][bgsrc];"
        f"[bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=20:2[bg];"
        f"{fg};"
        f"[bg][fg]overlay={vx}:{oy},setsar=1[outv]"
    )


def _canvas_for_aspect(render: RenderConfig, aspect: str) -> tuple[int, int, float]:
    """Return ``(width, height, card_aspect)`` for the requested output aspect.

    9:16 is the vertical canvas with a 1:1 card; 16:9 swaps to a horizontal
    canvas with a 16:9 card so horizontal (educational) frames are kept whole.
    """
    long_side = max(render.shorts_width, render.shorts_height)
    short_side = min(render.shorts_width, render.shorts_height)
    if aspect == "16:9":
        return long_side, short_side, 16 / 9
    return short_side, long_side, 1.0


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
    style: str = "fit",
    card_background: str = "blur",
    aspect: str = "9:16",
) -> Path:
    """Render ``[start, end]`` of ``source`` as a short into ``dest``.

    ``aspect`` is ``"9:16"`` (vertical) or ``"16:9"`` (horizontal). ``style``
    selects the framing:

    * ``"fit"`` (default) — the whole frame is fitted (never cropped) over a
      blurred fill of itself, so slides/code/diagrams stay fully visible.
    * ``"card"`` — the frame sits as a rounded card centred on the canvas with a
      visible margin, over a blurred (``card_background="blur"``) or solid black
      (``"black"``) background. The card is 1:1 for a 9:16 canvas and 16:9 for a
      16:9 canvas.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end - start)
    w, h, card_ar = _canvas_for_aspect(render, aspect)
    if style == "card":
        graph = _card_graph(
            render, duration=duration, background=card_background, w=w, h=h, card_ar=card_ar
        )
    else:
        graph = _fit_graph(w, h, blur=render.shorts_blur_background)

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
