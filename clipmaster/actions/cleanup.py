"""Cleanup action: render a trimmed cut that keeps only the worthwhile footage.

The keep-spans were already decided during analysis (``cleanup_keep_spans`` on the
report), fusing transcript importance, silence, filler / off-topic detection and
on-screen visual activity. This module just realises that edit decision list as a
real, shorter video — it never removes footage that analysis chose to keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.actions._ffmpeg_ops import keep_and_concat, slugify
from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage
from clipmaster.logging_setup import get_logger
from clipmaster.models import AnalysisReport

logger = get_logger("actions.cleanup")


@dataclass
class CleanupResult:
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    kept_seconds: float = 0.0
    removed_seconds: float = 0.0
    message: str = ""


def _merge_spans(
    spans: list[tuple[float, float]], *, gap_tolerance: float = 0.4
) -> list[tuple[float, float]]:
    """Sort and coalesce overlapping / near-adjacent spans."""
    ordered = sorted((s, e) for s, e in spans if e > s)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start - merged[-1][1] <= gap_tolerance:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_cleanup(
    report: AnalysisReport,
    settings: Settings,
    *,
    output_dir: Path,
    bus: EventBus | None = None,
) -> CleanupResult:
    """Render the cleaned-up cut for ``report`` into ``output_dir``."""
    bus = bus or EventBus()
    source = Path(report.source_path)
    if not source.is_file():
        raise FileNotFoundError(
            f"The original video is no longer at {report.source_path}. "
            "Move it back or re-run the analysis to clean up."
        )

    raw_spans = [(s.start, s.end) for s in report.cleanup_keep_spans]
    spans = _merge_spans([(s, e) for s, e in raw_spans if e > s])
    if not spans:
        raise ValueError(
            "This project has no cleanup plan — re-run the analysis with content "
            "understanding enabled (uncheck 'transcript + silence only') so "
            "ClipMaster knows which parts to keep."
        )

    duration = report.media.duration_s
    kept = sum(e - s for s, e in spans)
    removed = max(0.0, duration - kept)
    pct = (removed / duration * 100) if duration else 0.0

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{slugify(source.stem, fallback='video')}-clean{source.suffix or '.mp4'}"
    if dest.suffix.lower() not in (".mp4", ".mov", ".mkv"):
        dest = dest.with_suffix(".mp4")

    bus.stage_start(
        Stage.CLEANUP,
        f"Removing {removed:.0f}s of silence / off-topic ({pct:.0f}% shorter)…",
        spans=len(spans),
    )

    def _on_progress(fraction: float) -> None:
        bus.progress(Stage.CLEANUP, fraction, "Rendering the cleaned cut…")

    has_audio = report.media.has_audio
    keep_and_concat(
        source,
        spans,
        dest,
        has_audio=has_audio,
        render=settings.render,
        ffmpeg_bin=settings.media.ffmpeg_bin,
        on_progress=_on_progress,
    )

    message = (
        f"Kept {kept:.0f}s of {duration:.0f}s — the cut is {pct:.0f}% shorter "
        f"across {len(spans)} kept span(s)."
    )
    bus.stage_end(Stage.CLEANUP, message, output=str(dest))
    logger.info("Cleanup rendered %s (%s)", dest, message)

    return CleanupResult(
        output_dir=output_dir,
        files=[dest],
        kept_seconds=kept,
        removed_seconds=removed,
        message=message,
    )
