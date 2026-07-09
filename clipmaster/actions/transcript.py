"""Transcript action: export the analysed transcript as readable files.

Unlike :func:`clipmaster.actions.notes.build_notes` (which rewrites the content
into study notes with the LLM), this action simply renders the *verbatim*
transcript so a user who only wants the words out of a video can get them. It
produces a readable ``transcript.md`` (grouped by chapter, with optional
timestamps) plus a plain ``transcript.txt`` of the full text — no LLM required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage
from clipmaster.logging_setup import get_logger
from clipmaster.models import AnalysisReport, Chapter, TranscriptSegment

logger = get_logger("actions.transcript")


@dataclass
class TranscriptResult:
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    message: str = ""


def _fmt_ts(seconds: float) -> str:
    """Format a timestamp as ``mm:ss`` (or ``h:mm:ss`` past an hour)."""
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _segments_for(report: AnalysisReport, chapter: Chapter) -> list[TranscriptSegment]:
    ids = set(chapter.segment_ids)
    out: list[TranscriptSegment] = []
    for seg in report.transcript.segments:
        in_chapter = seg.id in ids if ids else (chapter.start <= seg.start < chapter.end)
        if in_chapter and seg.text.strip():
            out.append(seg)
    return out


def _reflow(segments: list[TranscriptSegment], *, sentences_per_para: int = 5) -> list[str]:
    """Group verbatim segment text into readable paragraphs (no timestamps)."""
    text = " ".join(s.text.strip() for s in segments if s.text.strip())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    paras: list[str] = []
    for i in range(0, len(sentences), sentences_per_para):
        chunk = " ".join(sentences[i : i + sentences_per_para]).strip()
        if chunk:
            paras.append(chunk)
    return paras or ([text] if text else [])


def _render_markdown(report: AnalysisReport, *, include_timestamps: bool) -> str:
    stem = Path(report.source_path).stem or "Transcript"
    lines = [f"# Transcript — {stem}", ""]
    meta: list[str] = []
    if report.transcript.language:
        meta.append(f"**Language:** {report.transcript.language}")
    if report.media.duration_s:
        meta.append(f"**Duration:** {_fmt_ts(report.media.duration_s)}")
    if meta:
        lines += [" · ".join(meta), ""]

    chapters = list(report.chapters)
    if not chapters and report.transcript.segments:
        chapters = [
            Chapter(
                title=stem,
                start=report.transcript.segments[0].start,
                end=report.transcript.segments[-1].end,
                segment_ids=[s.id for s in report.transcript.segments],
            )
        ]

    for chapter in chapters:
        segs = _segments_for(report, chapter)
        if not segs:
            continue
        if len(chapters) > 1 or chapter.title != stem:
            lines += [f"## {chapter.title}", ""]
        if include_timestamps:
            for seg in segs:
                lines.append(f"`[{_fmt_ts(seg.start)}]` {seg.text.strip()}")
            lines.append("")
        else:
            for para in _reflow(segs):
                lines += [para, ""]

    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)


def build_transcript(
    report: AnalysisReport,
    settings: Settings,
    *,
    output_dir: Path,
    bus: EventBus | None = None,
    include_timestamps: bool = True,
) -> TranscriptResult:
    """Write the verbatim transcript for ``report`` into ``output_dir``."""
    bus = bus or EventBus()
    if not report.transcript.segments:
        raise ValueError(
            "There is no transcript to export. Analyse the video first."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    bus.stage_start(Stage.NOTES, "Exporting the transcript…", files=2)

    md_path = output_dir / "transcript.md"
    md_path.write_text(
        _render_markdown(report, include_timestamps=include_timestamps), encoding="utf-8"
    )
    bus.progress(Stage.NOTES, 0.5, "Wrote transcript.md")

    txt_path = output_dir / "transcript.txt"
    txt_path.write_text(report.transcript.full_text + "\n", encoding="utf-8")
    bus.progress(Stage.NOTES, 1.0, "Wrote transcript.txt")

    files = [md_path, txt_path]
    message = f"Exported the transcript ({len(files)} file(s))."
    bus.stage_end(Stage.NOTES, message, output=str(output_dir))
    logger.info("Transcript written to %s", output_dir)
    return TranscriptResult(output_dir=output_dir, files=files, message=message)
