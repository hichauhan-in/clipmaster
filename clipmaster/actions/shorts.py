"""Shorts action: cut vertical short-form clips from the strongest moments.

The desktop app asks the user for a soft duration range (e.g. 10–30s). We pick the
best candidate moments the analysis already found, fit each into that range, and
render them as generic 9:16 shorts (letterboxed over a blurred fill). This is the
neutral default template; a caller can supply a specific style later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.actions._ffmpeg_ops import render_vertical_short, slugify
from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage
from clipmaster.logging_setup import get_logger
from clipmaster.models import AnalysisReport

logger = get_logger("actions.shorts")


@dataclass
class ShortSpec:
    start: float
    end: float
    title: str
    hook: str = ""
    score: float = 0.5

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ShortsResult:
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    message: str = ""


def _fit_to_range(
    start: float, end: float, *, min_s: float, max_s: float, duration: float
) -> tuple[float, float]:
    """Centre-adjust a span so its length lands within ``[min_s, max_s]``."""
    length = end - start
    target = max(min_s, min(max_s, length))
    if length > max_s:
        mid = (start + end) / 2
        start, end = mid - target / 2, mid + target / 2
    elif length < min_s:
        mid = (start + end) / 2
        start, end = mid - target / 2, mid + target / 2
    # Clamp to the video, preserving the target length where possible.
    if start < 0:
        start, end = 0.0, min(duration, target)
    if end > duration:
        end = duration
        start = max(0.0, end - target)
    return start, end


def _select_spans(
    report: AnalysisReport, *, min_s: float, max_s: float, count: int
) -> list[ShortSpec]:
    duration = report.media.duration_s
    specs: list[ShortSpec] = []

    def _add(start: float, end: float, title: str, hook: str, score: float) -> None:
        s, e = _fit_to_range(start, end, min_s=min_s, max_s=max_s, duration=duration)
        if e - s < min(min_s, duration) - 0.1:
            return
        # Skip near-duplicates that start within 2s of an already chosen short.
        if any(abs(s - spec.start) < 2.0 for spec in specs):
            return
        specs.append(ShortSpec(start=s, end=e, title=title, hook=hook, score=score))

    candidates = sorted(report.clip_candidates, key=lambda c: c.score, reverse=True)
    for c in candidates:
        if len(specs) >= count:
            break
        _add(c.start, c.end, c.title or "Short", c.hook, c.score)

    if len(specs) < count and report.chapters:
        for ch in report.chapters:
            if len(specs) >= count:
                break
            _add(ch.start, min(ch.end, ch.start + max_s), ch.title or "Highlight", ch.summary, 0.4)

    if not specs and duration > 0:
        # Nothing analysed — fall back to evenly spaced windows.
        n = max(1, min(count, int(duration // max(1.0, min_s)) or 1))
        step = duration / n
        for i in range(n):
            mid = step * (i + 0.5)
            _add(mid - max_s / 2, mid + max_s / 2, f"Clip {i + 1}", "", 0.3)

    return specs[:count]


def build_shorts(
    report: AnalysisReport,
    settings: Settings,
    *,
    min_seconds: float,
    max_seconds: float,
    count: int | None = None,
    output_dir: Path,
    bus: EventBus | None = None,
) -> ShortsResult:
    """Render up to ``count`` vertical shorts into ``output_dir``."""
    bus = bus or EventBus()
    source = Path(report.source_path)
    if not source.is_file():
        raise FileNotFoundError(
            f"The original video is no longer at {report.source_path}. "
            "Move it back or re-run the analysis to make shorts."
        )

    min_s = max(3.0, min(min_seconds, max_seconds))
    max_s = max(min_s, min(max_seconds, 180.0))
    target = count or settings.clips.target_count
    specs = _select_spans(report, min_s=min_s, max_s=max_s, count=target)
    if not specs:
        raise ValueError("Could not find any moment to turn into a short.")

    output_dir.mkdir(parents=True, exist_ok=True)
    bus.stage_start(
        Stage.CLIPS, f"Rendering {len(specs)} short(s) ({min_s:.0f}–{max_s:.0f}s)…", count=len(specs)
    )

    has_audio = report.media.has_audio
    files: list[Path] = []
    n = len(specs)
    for i, spec in enumerate(specs):
        dest = output_dir / f"short-{i + 1:02d}-{slugify(spec.title, fallback='clip')}.mp4"

        def _on_progress(fraction: float, _i: int = i) -> None:
            bus.progress(
                Stage.CLIPS,
                (_i + fraction) / n,
                f"Short {_i + 1}/{n} · {spec.title}",
            )

        render_vertical_short(
            source,
            spec.start,
            spec.end,
            dest,
            has_audio=has_audio,
            render=settings.render,
            ffmpeg_bin=settings.media.ffmpeg_bin,
            on_progress=_on_progress,
        )
        files.append(dest)
        logger.info("Rendered short %s (%.1fs)", dest.name, spec.duration)

    message = f"Rendered {len(files)} short(s), {min_s:.0f}–{max_s:.0f}s each, as 9:16 video."
    bus.stage_end(Stage.CLIPS, message)
    return ShortsResult(output_dir=output_dir, files=files, message=message)
