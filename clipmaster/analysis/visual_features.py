"""Keyframe-based visual analysis — the *visual* signal of the analysis.

A transcript is blind to everything shown on screen. Yet a huge part of an
educational / work video's value is visual: slides, software demos, code and
terminals, lab or hardware setups, diagrams. Those moments must be treated as
*important* even when the speaker goes quiet — never dropped.

This module:

1. finds scene changes with ffmpeg (cheap, deterministic),
2. samples keyframes (scene changes + a regular cadence, capped for speed),
3. asks a local vision model (Ollama, e.g. ``qwen2.5vl:7b``) to classify each
   frame's *kind* and *informativeness*.

It degrades gracefully: if the vision model isn't reachable/installed it still
returns the scene-change timeline (keyframes empty), and the fused importance
simply re-weights the remaining signals.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from clipmaster.analysis.ollama_client import OllamaClient, OllamaError
from clipmaster.config import Settings
from clipmaster.logging_setup import get_logger
from clipmaster.media.ffmpeg import FFmpegError, run_ffmpeg
from clipmaster.models import MediaInfo, VisualFeatures, VisualKeyframe, VisualKind

logger = get_logger("analysis.visual")

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_VALID_KINDS = {k.value for k in VisualKind}

_SYSTEM_PROMPT = (
    "You are a precise video-frame analyst for an educational/work video. "
    "You look at ONE frame and return STRICT JSON only."
)

_FRAME_PROMPT = (
    "Classify this single video frame. Return JSON with EXACTLY these keys:\n"
    "{\n"
    '  "kind": one of ["presentation","screen_demo","code_terminal",'
    '"lab_hardware","diagram_chart","talking_head","other"],\n'
    '  "description": "one short sentence describing what is on screen",\n'
    '  "has_text": true or false,\n'
    '  "informativeness": a number from 0.0 to 1.0\n'
    "}\n"
    "Guidance: slides, software/screen demos, code or terminals, lab/hardware "
    "setups and diagrams show teaching content and are HIGH informativeness "
    "(0.7-1.0). A plain presenter on camera with nothing else on screen is "
    "'talking_head' with LOW informativeness (0.2-0.4). Judge informativeness by "
    "how much unique visual information a viewer would lose if this exact moment "
    "were cut from the video."
)


def analyze_visual(
    source: str | Path,
    media: MediaInfo,
    settings: Settings,
    *,
    project_dir: str | Path,
    progress=None,
) -> VisualFeatures | None:
    """Analyse on-screen content, or ``None`` when there is no video stream."""
    if media.video is None:
        return None

    ac = settings.analysis
    vision_model = settings.llm.vision_model
    duration = media.duration_s or 0.0
    if duration <= 0:
        return None

    scene_changes = _detect_scene_changes(
        source, settings.media.ffmpeg_bin, ac.visual_scene_threshold
    )

    client = OllamaClient(
        host=settings.llm.host,
        model=vision_model,
        temperature=settings.llm.temperature,
        timeout=settings.llm.request_timeout_seconds,
    )
    if not _vision_model_available(client, vision_model):
        logger.warning(
            "Vision model %r not reachable/installed; visual analysis limited to "
            "scene changes. Pull it with `ollama pull %s`.",
            vision_model,
            vision_model,
        )
        return VisualFeatures(scene_changes=scene_changes, keyframes=[], model=vision_model)

    times = _keyframe_times(
        duration, scene_changes, ac.visual_sample_seconds, ac.visual_max_frames
    )
    frames_dir = Path(project_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    keyframes: list[VisualKeyframe] = []
    total = len(times)
    for i, t in enumerate(times):
        if progress is not None:
            progress(i / max(1, total), f"Analyzing frame {i + 1}/{total}")
        dest = frames_dir / f"frame_{i:03d}.jpg"
        try:
            _extract_frame(source, dest, t, settings.media.ffmpeg_bin)
            image_b64 = base64.b64encode(dest.read_bytes()).decode("ascii")
            data = client.vision_json(
                _FRAME_PROMPT, image_b64, system=_SYSTEM_PROMPT, model=vision_model
            )
            keyframes.append(_parse_frame(data, t, dest))
        except (FFmpegError, OllamaError, OSError) as exc:
            logger.warning("Keyframe at %.1fs failed: %s", t, exc)

    if progress is not None:
        progress(1.0, f"{len(keyframes)} keyframe(s) analysed")

    return VisualFeatures(
        scene_changes=scene_changes, keyframes=keyframes, model=vision_model
    )


# --- ffmpeg helpers ----------------------------------------------------------
def _detect_scene_changes(
    source: str | Path, ffmpeg_bin: str, threshold: float
) -> list[float]:
    """Return timestamps (s) where the picture changes significantly."""
    args = [
        "-i",
        str(source),
        "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_ffmpeg(ffmpeg_bin, args)
    except FFmpegError as exc:
        logger.warning("Scene detection failed: %s", exc)
        return []
    times: set[float] = set()
    for line in (result.stderr or "").splitlines():
        if "showinfo" in line and (m := _PTS_RE.search(line)) is not None:
            times.add(round(float(m.group(1)), 2))
    return sorted(times)


def _keyframe_times(
    duration: float,
    scene_changes: list[float],
    sample_seconds: float,
    max_frames: int,
) -> list[float]:
    """Combine a regular sampling cadence with scene changes, capped in count."""
    times: set[float] = set()
    step = max(1.0, sample_seconds)
    t = step / 2
    while t < duration:
        times.add(round(t, 2))
        t += step
    for s in scene_changes:
        if 0.0 <= s < duration:
            times.add(round(min(s + 0.2, duration - 0.05), 2))  # land on the new shot

    ordered = sorted(times)
    if len(ordered) > max_frames > 0:
        picked = {
            ordered[round(i * (len(ordered) - 1) / (max_frames - 1))]
            for i in range(max_frames)
        }
        ordered = sorted(picked)
    return ordered


def _extract_frame(
    source: str | Path,
    dest: Path,
    time_s: float,
    ffmpeg_bin: str,
    *,
    max_width: int = 768,
) -> Path:
    """Grab a single JPEG frame at ``time_s`` (downscaled for the vision model)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-ss",
        f"{time_s:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-vf",
        f"scale='min({max_width},iw)':-2",
        str(dest),
    ]
    run_ffmpeg(ffmpeg_bin, args)
    return dest


# --- Parsing -----------------------------------------------------------------
def _vision_model_available(client: OllamaClient, model: str) -> bool:
    try:
        installed = client.list_models()
    except OllamaError:
        return False
    family = model.split(":")[0]
    return any(name == model or name.startswith(family) for name in installed)


def _clamp01(value: object, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_frame(data: object, time_s: float, path: Path) -> VisualKeyframe:
    kind = VisualKind.OTHER
    description = ""
    informativeness = 0.5
    has_text = False
    if isinstance(data, dict):
        raw_kind = str(data.get("kind", "")).strip().lower().replace(" ", "_")
        if raw_kind in _VALID_KINDS:
            kind = VisualKind(raw_kind)
        description = str(data.get("description", "")).strip()[:400]
        informativeness = _clamp01(data.get("informativeness"))
        has_text = bool(data.get("has_text") or data.get("on_screen_text"))
    return VisualKeyframe(
        time=time_s,
        kind=kind,
        description=description,
        informativeness=round(informativeness, 3),
        has_text=has_text,
        image_path=str(path),
    )
