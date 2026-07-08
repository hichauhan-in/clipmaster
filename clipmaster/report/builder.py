"""Build a readable Markdown summary from the machine-readable ``analysis.json``.

The JSON artifact is the source of truth consumed by code; this Markdown is for
humans skimming what the pipeline found before deciding what to do next.
"""

from __future__ import annotations

from pathlib import Path

from clipmaster.models import AnalysisReport


def format_timestamp(seconds: float) -> str:
    """Format seconds as ``H:MM:SS`` (or ``M:SS`` under an hour)."""
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render_markdown(report: AnalysisReport) -> str:
    """Return a Markdown document summarising ``report``."""
    m = report.media
    lines: list[str] = []

    lines.append(f"# Analysis — {Path(report.source_path).name}")
    lines.append("")
    lines.append(f"*Project `{report.project_id}` · generated {report.created_at}*")
    lines.append("")

    # --- Overview ---
    if report.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    # --- Media ---
    lines.append("## Media")
    lines.append("")
    res = f"{m.video.width}x{m.video.height}" if m.video and m.video.width else "unknown"
    fps = f"{m.video.fps:g}" if m.video and m.video.fps else "?"
    lines.append(f"- **Duration:** {format_timestamp(m.duration_s)} ({m.duration_s:.1f}s)")
    lines.append(f"- **Resolution:** {res} @ {fps} fps")
    lines.append(f"- **Container:** {m.container or 'unknown'} · **Size:** {_human_size(m.size_bytes)}")
    lines.append(f"- **Audio streams:** {len(m.audios)}")
    lines.append(
        f"- **Chunks processed:** {len(report.chunk_plan.chunks)} "
        f"(max {report.chunk_plan.max_chunk_seconds / 60:.0f} min each)"
    )
    lines.append(
        f"- **Transcription:** {report.transcription_model or 'n/a'} · "
        f"**LLM:** {report.llm_model or 'n/a (heuristics only)'}"
    )
    if report.vision_model:
        lines.append(f"- **Vision:** {report.vision_model}")
    lines.append("")

    # --- Keywords ---
    if report.keywords:
        lines.append("## Keywords")
        lines.append("")
        lines.append(", ".join(f"`{kw}`" for kw in report.keywords))
        lines.append("")

    # --- Chapters ---
    if report.chapters:
        lines.append("## Chapters")
        lines.append("")
        for ch in report.chapters:
            lines.append(
                f"### {format_timestamp(ch.start)} – {format_timestamp(ch.end)} · {ch.title}"
            )
            if ch.summary:
                lines.append("")
                lines.append(ch.summary)
            if ch.keywords:
                lines.append("")
                lines.append("*Keywords:* " + ", ".join(f"`{k}`" for k in ch.keywords))
            lines.append("")

    # --- On-screen content ---
    vf = report.visual_features
    if vf and vf.keyframes:
        kinds: dict[str, int] = {}
        for kf in vf.keyframes:
            kinds[kf.kind.value] = kinds.get(kf.kind.value, 0) + 1
        lines.append("## On-screen content")
        lines.append("")
        lines.append(
            f"*{len(vf.keyframes)} keyframe(s) analysed with "
            f"{vf.model or 'the vision model'} · {len(vf.scene_changes)} scene change(s)*"
        )
        lines.append("")
        dist = " · ".join(
            f"{n}× {k.replace('_', ' ')}"
            for k, n in sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)
        )
        lines.append(f"- **Frame types:** {dist}")
        notable = sorted(
            (kf for kf in vf.keyframes if kf.informativeness >= 0.6),
            key=lambda k: k.informativeness,
            reverse=True,
        )[:8]
        if notable:
            lines.append("")
            lines.append("**Notable visual moments:**")
            lines.append("")
            for kf in sorted(notable, key=lambda k: k.time):
                desc = kf.description or kf.kind.value.replace("_", " ")
                lines.append(
                    f"- {format_timestamp(kf.time)} · "
                    f"*{kf.kind.value.replace('_', ' ')}* — {desc}"
                )
        lines.append("")

    # --- Clip candidates ---
    if report.clip_candidates:
        lines.append("## Suggested Clips (shorts)")
        lines.append("")
        lines.append("| Score | Start | End | Length | Title | Hook |")
        lines.append("| ----: | :---- | :-- | -----: | :---- | :--- |")
        for c in report.clip_candidates:
            lines.append(
                f"| {c.score:.2f} | {format_timestamp(c.start)} | {format_timestamp(c.end)} "
                f"| {c.duration:.0f}s | {c.title} | {c.hook} |"
            )
        lines.append("")

    # --- Cleanup preview ---
    lines.append("## Cleanup Preview")
    lines.append("")
    removed = report.cleanup_removed_seconds
    kept = report.cleanup_kept_seconds
    pct = (removed / m.duration_s * 100) if m.duration_s else 0.0
    lines.append(
        f"- **Kept:** {format_timestamp(kept)} · **Removed:** {format_timestamp(removed)} "
        f"({pct:.0f}% shorter)"
    )
    lines.append(f"- **Silent spans detected:** {len(report.silences)}")
    n_filler = sum(1 for s in report.segment_analyses if s.kind.value == "filler")
    n_off = sum(1 for s in report.segment_analyses if s.kind.value == "off_topic")
    n_qa = sum(1 for s in report.segment_analyses if s.kind.value == "qa")
    lines.append(
        f"- **Segments flagged:** {n_filler} filler · {n_off} off-topic · {n_qa} Q&A"
    )
    n_visual_kept = sum(
        1 for s in report.segment_analyses if s.reason.startswith("On-screen")
    )
    if n_visual_kept:
        lines.append(f"- **Kept for on-screen content:** {n_visual_kept} segment(s)")
    lines.append("")

    # --- Warnings ---
    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


def write_markdown(report: AnalysisReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path
