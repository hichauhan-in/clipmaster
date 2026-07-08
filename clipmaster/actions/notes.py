"""Notes action: generate Markdown study notes from an analysed video.

Produces a small set of Markdown files — an index/study-guide plus one file per
chapter — with topics, subtopics, key points and (where it helps) a mermaid
diagram. The local LLM writes the prose; when it is unavailable we still emit
useful structural notes from the analysis so the action never fails silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.analysis.ollama_client import OllamaClient, OllamaError
from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage
from clipmaster.logging_setup import get_logger
from clipmaster.models import AnalysisReport, Chapter
from clipmaster.report.builder import format_timestamp

logger = get_logger("actions.notes")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MERMAID_KEYWORDS = ("flowchart", "graph", "sequencediagram", "mindmap", "classdiagram")

_SYSTEM = (
    "You are an expert study-notes writer. You convert a lecture transcript "
    "excerpt into faithful, well-structured study notes. Respond ONLY with strict "
    "JSON — no prose outside the JSON. Never invent facts that are not supported "
    "by the transcript."
)


@dataclass
class NotesResult:
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    message: str = ""
    used_llm: bool = False


def _slug(name: str, *, fallback: str = "chapter") -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")[:48].strip("-")
    return slug or fallback


def _sanitize_node(text: str, *, max_len: int = 48) -> str:
    """Make a label safe to drop into a mermaid mindmap node."""
    cleaned = re.sub(r"[()\[\]{}#\"|;]", "", text).replace("\n", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_len].strip() or "topic"


def _chapter_text(report: AnalysisReport, chapter: Chapter, *, limit: int) -> str:
    ids = set(chapter.segment_ids)
    parts: list[str] = []
    for seg in report.transcript.segments:
        in_chapter = seg.id in ids if ids else (chapter.start <= seg.start < chapter.end)
        if in_chapter:
            parts.append(seg.text.strip())
    text = " ".join(p for p in parts if p).strip()
    return text[:limit]


def _clean_mermaid(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    body = raw.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    first = body.lstrip().split(None, 1)[0].lower() if body.strip() else ""
    if first not in _MERMAID_KEYWORDS:
        return ""
    return body


def _llm_notes(client: OllamaClient, model: str, title: str, text: str) -> dict:
    prompt = (
        f"Chapter title: {title}\n\n"
        f'Transcript excerpt:\n"""\n{text}\n"""\n\n'
        "Return JSON exactly in this shape:\n"
        "{\n"
        '  "title": "a concise, descriptive chapter title",\n'
        '  "overview": "2-4 sentence plain-language overview",\n'
        '  "key_points": ["short factual takeaway", "..."],\n'
        '  "subtopics": [{"heading": "subtopic name", "points": ["detail", "..."]}],\n'
        '  "mermaid": "a single mermaid \'flowchart TD\' diagram of how the ideas '
        'connect, or an empty string if a diagram would not help"\n'
        "}"
    )
    data = client.chat_json(prompt, system=_SYSTEM, model=model)
    if not isinstance(data, dict):
        raise OllamaError("notes response was not a JSON object")
    return data


def _fallback_notes(title: str, text: str, chapter: Chapter) -> dict:
    """Structural notes from the analysis alone (no LLM)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 25]
    return {
        "title": title,
        "overview": chapter.summary or (sentences[0] if sentences else ""),
        "key_points": sentences[:6],
        "subtopics": [],
        "mermaid": "",
    }


def _coerce(data: dict, title: str) -> dict:
    def _str_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    subtopics: list[dict] = []
    for item in data.get("subtopics") or []:
        if isinstance(item, dict):
            heading = str(item.get("heading", "")).strip()
            points = _str_list(item.get("points"))
            if heading or points:
                subtopics.append({"heading": heading or "Details", "points": points})
    return {
        "title": str(data.get("title") or title).strip() or title,
        "overview": str(data.get("overview") or "").strip(),
        "key_points": _str_list(data.get("key_points")),
        "subtopics": subtopics,
        "mermaid": _clean_mermaid(data.get("mermaid")),
    }


def _render_chapter_md(chapter: Chapter, notes: dict) -> str:
    lines = [f"# {notes['title']}", ""]
    lines.append(f"*{format_timestamp(chapter.start)} – {format_timestamp(chapter.end)}*")
    lines.append("")
    if notes["overview"]:
        lines += ["## Overview", "", notes["overview"], ""]
    if notes["key_points"]:
        lines += ["## Key points", ""]
        lines += [f"- {p}" for p in notes["key_points"]]
        lines.append("")
    for sub in notes["subtopics"]:
        lines += [f"## {sub['heading']}", ""]
        lines += [f"- {p}" for p in sub["points"]]
        lines.append("")
    if notes["mermaid"]:
        lines += ["## Diagram", "", "```mermaid", notes["mermaid"], "```", ""]
    if chapter.keywords:
        lines += ["---", "", "*Keywords:* " + ", ".join(f"`{k}`" for k in chapter.keywords), ""]
    return "\n".join(lines)


def _render_index_md(
    report: AnalysisReport,
    entries: list[tuple[Chapter, str, str]],
    *,
    used_llm: bool,
) -> str:
    title = Path(report.source_path).stem
    lines = [f"# Study notes — {title}", ""]
    lines.append(
        f"*{len(entries)} chapter file(s) · source {format_timestamp(report.media.duration_s)}*"
    )
    lines.append("")
    if report.summary:
        lines += ["## Summary", "", report.summary, ""]
    if report.keywords:
        lines += ["## Keywords", "", ", ".join(f"`{k}`" for k in report.keywords), ""]

    lines += ["## Contents", ""]
    for chapter, notes_title, filename in entries:
        stamp = f"{format_timestamp(chapter.start)}–{format_timestamp(chapter.end)}"
        lines.append(f"- [{notes_title}]({filename}) · {stamp}")
    lines.append("")

    # Programmatic topic mindmap — reliable, no LLM needed.
    lines += ["## Topic map", "", "```mermaid", "mindmap", f"  root(({_sanitize_node(title)}))"]
    for chapter, notes_title, _ in entries:
        lines.append(f"    {_sanitize_node(notes_title)}")
        for kw in chapter.keywords[:4]:
            lines.append(f"      {_sanitize_node(kw)}")
    lines += ["```", ""]
    if not used_llm:
        lines += [
            "> These notes were generated from the analysis structure only "
            "(the local LLM was unavailable). Start Ollama and re-run for richer notes.",
            "",
        ]
    return "\n".join(lines)


def _chapters_for(report: AnalysisReport) -> list[Chapter]:
    if report.chapters:
        return list(report.chapters)
    # No chapters (e.g. transcript-only run): treat the whole transcript as one.
    if report.transcript.segments:
        return [
            Chapter(
                title=Path(report.source_path).stem or "Full transcript",
                start=report.transcript.segments[0].start,
                end=report.transcript.segments[-1].end,
                summary=report.summary,
                keywords=report.keywords,
                segment_ids=[s.id for s in report.transcript.segments],
            )
        ]
    return []


def build_notes(
    report: AnalysisReport,
    settings: Settings,
    *,
    output_dir: Path,
    bus: EventBus | None = None,
) -> NotesResult:
    """Generate Markdown study notes for ``report`` into ``output_dir``."""
    bus = bus or EventBus()
    chapters = _chapters_for(report)
    if not chapters:
        raise ValueError(
            "There is no transcript to make notes from. Analyse the video first."
        )

    client = OllamaClient(
        host=settings.llm.host,
        model=settings.llm.model,
        temperature=settings.llm.temperature,
        timeout=settings.llm.request_timeout_seconds,
    )
    use_llm = client.is_available()
    used_llm_any = False
    limit = settings.llm.max_input_chars

    output_dir.mkdir(parents=True, exist_ok=True)
    n = len(chapters)
    bus.stage_start(Stage.NOTES, f"Writing study notes for {n} chapter(s)…", chapters=n)

    entries: list[tuple[Chapter, str, str]] = []
    files: list[Path] = []
    for i, chapter in enumerate(chapters):
        text = _chapter_text(report, chapter, limit=limit)
        title = chapter.title or f"Chapter {i + 1}"
        bus.progress(Stage.NOTES, i / n, f"Notes {i + 1}/{n} · {title}")

        notes: dict | None = None
        if use_llm and text:
            try:
                notes = _coerce(_llm_notes(client, settings.llm.model, title, text), title)
                used_llm_any = True
            except Exception as exc:  # noqa: BLE001 - degrade to structural notes
                logger.warning("LLM notes failed for chapter %d: %s", i, exc)
                notes = None
        if notes is None:
            notes = _coerce(_fallback_notes(title, text, chapter), title)

        filename = f"{i + 1:02d}-{_slug(notes['title'])}.md"
        (output_dir / filename).write_text(_render_chapter_md(chapter, notes), encoding="utf-8")
        files.append(output_dir / filename)
        entries.append((chapter, notes["title"], filename))

    index = output_dir / "README.md"
    index.write_text(_render_index_md(report, entries, used_llm=used_llm_any), encoding="utf-8")
    files.insert(0, index)

    how = "with the local LLM" if used_llm_any else "from the analysis structure"
    message = f"Wrote {len(files)} Markdown file(s) {how}."
    bus.stage_end(Stage.NOTES, message, output=str(output_dir))
    logger.info("Notes written to %s (%s)", output_dir, message)

    return NotesResult(
        output_dir=output_dir, files=files, message=message, used_llm=used_llm_any
    )
