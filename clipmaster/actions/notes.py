"""Notes action: generate written study notes from an analysed video.

Produces a compact set of Markdown files — a study-guide index (``README.md``)
plus a handful of long, self-contained notes files — that read like proper
revision notes for interview prep or personal learning. The notes are written
prose (not a transcript dump and with no "in the video / at 03:12" references);
the local LLM does the writing, and when it is unavailable we still emit useful
notes reflowed from the analysis so the action never fails silently.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.analysis.ollama_client import OllamaClient, OllamaError
from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage
from clipmaster.logging_setup import get_logger
from clipmaster.models import AnalysisReport, Chapter

logger = get_logger("actions.notes")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Mermaid diagram declarations we accept — the first meaningful token of a block
# must be one of these. Deliberately limited to the diagram types that render
# reliably across common Markdown viewers (GitHub, VS Code, Obsidian). The newer
# experimental "*-beta" diagrams (packet-beta, block-beta, xychart-beta, …) and a
# few niche types are excluded because many renderers log a parse error on them,
# which is exactly the "diagram errors in between" problem we want to avoid.
_MERMAID_KEYWORDS = (
    "flowchart",
    "graph",
    "sequencediagram",
    "statediagram",
    "statediagram-v2",
    "classdiagram",
    "erdiagram",
    "mindmap",
    "timeline",
    "journey",
    "gantt",
)

# Node-label characters that commonly break unquoted mermaid flowchart labels.
_RISKY_LABEL_RE = re.compile(r"[()<>#&/:;|]")
# Fancy unicode dashes/arrows LLMs sometimes emit instead of ASCII.
_ARROW_FIXUPS = (
    ("\u2192", "-->"),  # →
    ("\u21d2", "-->"),  # ⇒
    ("\u2014", "--"),   # —
    ("\u2013", "--"),   # –
    ("\u2212", "-"),    # −
)

_SYSTEM = (
    "You are an expert study-notes author. You turn a lecture transcript excerpt "
    "into clear, well-structured written notes that someone could revise from for "
    "an interview or to learn the topic — full sentences and proper explanations, "
    "not a transcript and not terse fragments. Rules: (1) Write self-contained "
    "prose that teaches the concept; define terms, explain the 'why', and add "
    "brief examples where they help. (2) Where a process, protocol exchange, state "
    "machine, data structure or relationship is involved, include one or more "
    "diagrams and pick the most fitting mermaid type: 'sequenceDiagram' for "
    "message/handshake exchanges (e.g. a TLS handshake, request/response flows), "
    "'flowchart TD' for processes and decisions, 'stateDiagram-v2' for state "
    "machines, and 'classDiagram' or 'erDiagram' for structures/relationships. "
    "Prefer sequenceDiagram and flowchart — they are the most reliable. MERMAID "
    "SYNTAX RULES you MUST follow so diagrams never error: always wrap EVERY node "
    "label and edge label in double quotes, e.g. `A[\"Client\"] -->|\"ClientHello\"| "
    "B[\"Server\"]`; use only plain ASCII arrows (`-->`, `->>`, `-->>`, `--x`); "
    "never put parentheses, slashes, colons, `#`, `&`, `<` or `>` OUTSIDE quotes; "
    "give every node a short alphanumeric id (letters/digits, no spaces) and never "
    "use the reserved word `end` as an id; put the diagram type on its OWN line with "
    "NO direction after `sequenceDiagram`/`classDiagram`/`erDiagram`/`stateDiagram-v2` "
    "(only `flowchart`/`graph` take a direction such as `TD`/`LR`); keep every node "
    "label on a SINGLE line (use `<br/>` for a line break, never a real newline) and "
    "never place numbered or bulleted lists inside a diagram; keep each statement on "
    "its own line; do NOT use experimental diagram types. Every diagram must be "
    "COMPLETE and valid mermaid. (3) Never refer to 'the video', 'the speaker', "
    "'this section/lecture', 'as mentioned', or any timestamps — the reader has no "
    "access to the source. (4) Never invent facts that the transcript does not "
    "support. Respond ONLY with strict JSON — no prose outside the JSON."
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


def _quote_label(inner: str) -> str:
    """Wrap a mermaid node/edge label in double quotes, escaping inner quotes."""
    return '"' + inner.replace('"', "'").strip() + '"'


def _sanitize_flow_line(line: str) -> str:
    """Quote risky, unquoted labels in a flowchart/graph line so it won't error.

    Handles the common single-bracket node shapes ``[..]``, ``(..)``, ``{..}`` and
    pipe edge labels ``|..|``. Labels that already look quoted, or that contain no
    risky characters, are left untouched. Double-bracket shapes (``[[..]]``,
    ``((..))``) are guarded against with a negative lookahead.
    """

    def _repl_bracket(open_ch: str, close_ch: str) -> "re.Pattern[str]":
        # id/text then a single opening bracket (not doubled), inner without the
        # SAME bracket kind, then the matching closer.
        o, c = re.escape(open_ch), re.escape(close_ch)
        return re.compile(rf"{o}(?!{o})([^{o}{c}\n]+?){c}")

    def _sub(match: "re.Match[str]", open_ch: str, close_ch: str) -> str:
        inner = match.group(1)
        stripped = inner.strip()
        if not stripped or (stripped.startswith('"') and stripped.endswith('"')):
            return match.group(0)
        if not _RISKY_LABEL_RE.search(inner):
            return match.group(0)
        return f"{open_ch}{_quote_label(inner)}{close_ch}"

    for open_ch, close_ch in (("[", "]"), ("(", ")"), ("{", "}")):
        pat = _repl_bracket(open_ch, close_ch)
        line = pat.sub(lambda m, o=open_ch, c=close_ch: _sub(m, o, c), line)

    # Pipe edge labels: A -->|label| B  ->  A -->|"label"| B
    def _sub_pipe(match: "re.Match[str]") -> str:
        inner = match.group(1)
        stripped = inner.strip()
        if not stripped or (stripped.startswith('"') and stripped.endswith('"')):
            return match.group(0)
        if not _RISKY_LABEL_RE.search(inner):
            return match.group(0)
        return f"|{_quote_label(inner)}|"

    line = re.sub(r"\|([^|\n]+?)\|", _sub_pipe, line)
    return line


_FLOW_SKIP_FIRST = (
    "flowchart",
    "graph",
    "subgraph",
    "end",
    "style",
    "classdef",
    "class",
    "linkstyle",
    "direction",
)


def _sanitize_mermaid(body: str, kind: str) -> str:
    """Best-effort cleanup so LLM-authored mermaid renders without parse errors."""
    for bad, good in _ARROW_FIXUPS:
        body = body.replace(bad, good)
    # Strip markdown emphasis/backticks that sometimes leak into a diagram.
    body = body.replace("`", "")
    is_flow = kind in ("flowchart", "graph")
    out: list[str] = []
    seen_decl = False
    for line in body.splitlines():
        s = line.rstrip()
        stripped = s.strip()
        if (
            not seen_decl
            and stripped
            and not stripped.startswith("%%")
            and stripped != "---"
            and not stripped.startswith(("title:", "config:"))
        ):
            # Declaration line. Only flowchart/graph may carry a direction; for
            # every other type a trailing 'TD'/'LR'/… is invalid (the reported
            # `sequenceDiagram TD` error), so keep just the diagram-type token.
            seen_decl = True
            tok = stripped.split(None, 1)[0]
            if tok.lower() in _MERMAID_KEYWORDS and not is_flow:
                out.append(tok)
                continue
            out.append(s)
            continue
        if is_flow and stripped and not stripped.startswith("%%"):
            first = stripped.split(None, 1)[0].lower()
            # Don't touch the declaration/subgraph/style lines.
            if first not in _FLOW_SKIP_FIRST:
                s = _sanitize_flow_line(s)
        out.append(s)
    return "\n".join(out).strip()


def _strip_quoted(text: str) -> str:
    """Blank out double-quoted spans so structural checks ignore label contents."""
    return re.sub(r'"[^"\n]*"', '""', text)


def _mermaid_is_safe(text: str, kind: str) -> bool:
    """Reject diagrams with structural problems we cannot safely auto-repair.

    Emitting a broken diagram makes the whole note render an error block, so when
    a diagram looks malformed we drop it: the prose stays and a single
    illustration is simply omitted rather than shipped broken.
    """
    outside = _strip_quoted(text)
    for open_ch, close_ch in (("[", "]"), ("(", ")"), ("{", "}")):
        if outside.count(open_ch) != outside.count(close_ch):
            return False
    if kind not in ("flowchart", "graph"):
        return True
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("%%"):
            continue
        bare = _strip_quoted(s)
        # Markdown ordered-list leakage or a stray bare number is not valid mermaid
        # (this is the reported "got '1'" error from a multi-line/list label).
        if re.match(r"^\d+[.)]\s", bare) or re.fullmatch(r"\d+", bare):
            return False
        # A node shape must open and close on the same line (no multi-line labels).
        for open_ch, close_ch in (("[", "]"), ("(", ")"), ("{", "}")):
            if bare.count(open_ch) != bare.count(close_ch):
                return False
    return True


def _clean_mermaid(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    body = raw.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    if not body:
        return ""
    # Find the diagram declaration line, skipping YAML frontmatter, %% comments
    # and %%{init}%% directives, then check its first token is a known diagram.
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("%%") or s in ("---",) or s.startswith(("title:", "config:")):
            continue
        first = s.split(None, 1)[0].lower()
        if first not in _MERMAID_KEYWORDS:
            return ""
        cleaned = _sanitize_mermaid(body, first)
        # Require content beyond the declaration AND a structurally sound diagram —
        # a doubtful diagram is dropped so the markdown never renders an error.
        content = [ln for ln in cleaned.splitlines() if ln.strip()]
        if len(content) < 2 or not _mermaid_is_safe(cleaned, first):
            return ""
        return cleaned
    return ""


def _reflow_paragraphs(text: str, *, sentences_per_para: int = 4) -> list[str]:
    """Group a run-on transcript into readable paragraphs of full sentences."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
    paragraphs: list[str] = []
    for i in range(0, len(sentences), sentences_per_para):
        chunk = " ".join(sentences[i : i + sentences_per_para]).strip()
        if chunk:
            paragraphs.append(chunk)
    return paragraphs


def _llm_notes(client: OllamaClient, model: str, title: str, text: str) -> dict:
    prompt = (
        f"Topic title: {title}\n\n"
        f'Transcript excerpt to write notes from:\n"""\n{text}\n"""\n\n'
        "Write thorough study notes for this topic and return JSON exactly in "
        "this shape:\n"
        "{\n"
        '  "title": "a concise, descriptive topic title",\n'
        '  "summary": "1-2 sentence plain-language summary of the topic",\n'
        '  "sections": [\n'
        "    {\n"
        '      "heading": "a sub-topic or concept name",\n'
        '      "content": "one or more full paragraphs of written explanation in '
        "Markdown. Teach the concept clearly: define terms, explain how and why it "
        "works, and include short concrete examples where useful. You may use "
        '"- " bullet lines inside the content for lists of steps or items."\n'
        "    }\n"
        "  ],\n"
        '  "diagrams": [\n'
        "    {\n"
        '      "title": "short caption for the diagram",\n'
        '      "mermaid": "a COMPLETE, valid mermaid diagram. Choose the type that '
        "fits: sequenceDiagram for handshakes / protocol message exchanges / "
        "request-response flows, flowchart TD for processes and decisions, "
        "stateDiagram-v2 for state machines, classDiagram or erDiagram for "
        "structures. ALWAYS wrap every node and edge label in double quotes and "
        "use only plain ASCII arrows so it never errors, e.g. "
        '\\"A[\\\\\\"Client\\\\\\"] -->|\\\\\\"ClientHello\\\\\\"| B[\\\\\\"Server\\\\\\"]\\"."\n'
        "    }\n"
        "  ],\n"
        '  "key_takeaways": ["a concise revision point", "..."]\n'
        "}\n\n"
        "Write in real depth: aim for at least three well-developed sections that "
        "actually teach the topic (definitions, how it works step by step, why it "
        "matters, and a concrete example). Include 1-3 diagrams whenever a flow, "
        "exchange or structure would make the topic clearer (use an empty list "
        "only if no diagram helps). Do not mention the video, the speaker, or any "
        "timestamps."
    )
    data = client.chat_json(prompt, system=_SYSTEM, model=model)
    if not isinstance(data, dict):
        raise OllamaError("notes response was not a JSON object")
    return data


def _fallback_notes(title: str, text: str, chapter: Chapter) -> dict:
    """Readable notes reflowed from the analysis alone (no LLM)."""
    paragraphs = _reflow_paragraphs(text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 25]
    sections: list[dict] = []
    if paragraphs:
        sections.append({"heading": "Notes", "content": "\n\n".join(paragraphs)})
    return {
        "title": title,
        "summary": chapter.summary or (sentences[0] if sentences else ""),
        "sections": sections,
        "diagrams": [],
        "key_takeaways": sentences[:6],
    }


def _coerce_diagrams(data: dict) -> list[dict]:
    """Normalise the ``diagrams`` field into ``[{title, mermaid}]`` (valid only)."""
    diagrams: list[dict] = []
    raw = data.get("diagrams")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                mermaid = _clean_mermaid(
                    item.get("mermaid") or item.get("code") or item.get("diagram")
                )
                title = str(item.get("title") or item.get("caption") or "").strip()
            elif isinstance(item, str):
                mermaid = _clean_mermaid(item)
                title = ""
            else:
                continue
            if mermaid:
                diagrams.append({"title": title, "mermaid": mermaid})
    # Back-compat: a single top-level "mermaid" string.
    if not diagrams:
        mermaid = _clean_mermaid(data.get("mermaid"))
        if mermaid:
            diagrams.append({"title": "", "mermaid": mermaid})
    return diagrams


def _coerce(data: dict, title: str) -> dict:
    def _str_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    sections: list[dict] = []
    for item in data.get("sections") or []:
        if isinstance(item, dict):
            heading = str(item.get("heading", "")).strip()
            content = str(item.get("content", "")).strip()
            if heading or content:
                sections.append({"heading": heading or "Details", "content": content})
    return {
        "title": str(data.get("title") or title).strip() or title,
        "summary": str(data.get("summary") or data.get("overview") or "").strip(),
        "sections": sections,
        "diagrams": _coerce_diagrams(data),
        "key_takeaways": _str_list(data.get("key_takeaways") or data.get("key_points")),
        "screenshots": [],
    }


def _chapter_screenshots(
    report: AnalysisReport,
    chapter: Chapter,
    informative_kinds: set[str],
    images_dir: Path,
    *,
    max_shots: int = 3,
    min_info: float = 0.5,
) -> list[tuple[str, str]]:
    """Copy the most informative on-screen frames for this topic into the notes.

    Returns ``[(relative_image_path, caption)]``. Frames are chosen from the
    topic's time range, ranked by informativeness, de-duplicated by description,
    and copied into ``images_dir`` so the notes folder is self-contained.
    """
    vf = report.visual_features
    if vf is None or not vf.keyframes:
        return []
    candidates = [
        kf
        for kf in vf.keyframes
        if chapter.start <= kf.time < chapter.end
        and kf.kind.value in informative_kinds
        and kf.informativeness >= min_info
        and kf.image_path
    ]
    candidates.sort(key=lambda k: k.informativeness, reverse=True)

    picked = []
    seen: set[str] = set()
    for kf in candidates:
        key = (kf.description or "").strip().lower()[:60]
        if key and key in seen:
            continue
        if not Path(kf.image_path or "").is_file():
            continue
        seen.add(key)
        picked.append(kf)
        if len(picked) >= max_shots:
            break

    picked.sort(key=lambda k: k.time)  # reading order = chronological
    shots: list[tuple[str, str]] = []
    for kf in picked:
        src = Path(kf.image_path or "")
        images_dir.mkdir(parents=True, exist_ok=True)
        dest = images_dir / src.name
        try:
            if not dest.exists():
                shutil.copy2(src, dest)
        except OSError as exc:  # noqa: PERF203 - skip a single bad frame
            logger.warning("Could not copy screenshot %s: %s", src, exc)
            continue
        caption = kf.description.strip() or kf.kind.value.replace("_", " ")
        shots.append((f"images/{dest.name}", caption))
    return shots


def _render_chapter_section(notes: dict, chapter: Chapter, *, level: int = 2) -> list[str]:
    """Render one topic's notes as Markdown lines, headed at ``level`` (## by default)."""
    h = "#" * level
    sub = "#" * (level + 1)
    lines = [f"{h} {notes['title']}", ""]
    if notes["summary"]:
        lines += [notes["summary"], ""]
    for section in notes["sections"]:
        lines += [f"{sub} {section['heading']}", ""]
        if section["content"]:
            lines += [section["content"], ""]
    for i, diagram in enumerate(notes.get("diagrams", []), start=1):
        heading = diagram.get("title") or ("Diagram" if i == 1 else f"Diagram {i}")
        lines += [f"{sub} {heading}", "", "```mermaid", diagram["mermaid"], "```", ""]
    shots = notes.get("screenshots", [])
    if shots:
        lines += [f"{sub} Illustrations", ""]
        for rel, caption in shots:
            lines += [f"![{caption}]({rel})", ""]
            if caption:
                lines += [f"*{caption}*", ""]
    if notes["key_takeaways"]:
        lines += ["**Key takeaways**", ""]
        lines += [f"- {p}" for p in notes["key_takeaways"]]
        lines.append("")
    if chapter.keywords:
        lines += ["*Keywords:* " + ", ".join(f"`{k}`" for k in chapter.keywords), ""]
    return lines



def _render_group_md(group_title: str, items: list[tuple[Chapter, dict]]) -> str:
    """Render a single notes file that covers a group of consecutive topics."""
    lines = [f"# {group_title}", ""]
    multi = len(items) > 1
    for chapter, notes in items:
        # In multi-topic files each topic is a level-2 section; in a single-topic
        # file the topic title is already the H1, so start its detail at level 2 too.
        lines += _render_chapter_section(notes, chapter, level=2)
        if multi:
            lines += ["---", ""]
    # Drop a trailing separator so files don't end on a rule.
    while lines and lines[-1] in ("", "---"):
        lines.pop()
    lines.append("")
    return "\n".join(lines)


def _render_index_md(
    report: AnalysisReport,
    files: list[tuple[str, str, list[str]]],
    *,
    used_llm: bool,
) -> str:
    """Study-guide index: ``files`` is (filename, group_title, [topic titles])."""
    title = Path(report.source_path).stem
    lines = [f"# Study notes — {title}", ""]
    if report.summary:
        lines += ["## Overview", "", report.summary, ""]
    if report.keywords:
        lines += ["## Keywords", "", ", ".join(f"`{k}`" for k in report.keywords), ""]

    lines += ["## Contents", ""]
    for filename, group_title, topics in files:
        lines.append(f"- [{group_title}]({filename})")
        if len(topics) > 1:
            lines += [f"  - {t}" for t in topics]
    lines.append("")

    # Programmatic topic mindmap — reliable, no LLM needed.
    lines += ["## Topic map", "", "```mermaid", "mindmap", f"  root(({_sanitize_node(title)}))"]
    for _, group_title, topics in files:
        for topic in topics:
            lines.append(f"    {_sanitize_node(topic)}")
    lines += ["```", ""]
    if not used_llm:
        lines += [
            "> These notes were reflowed from the analysis only (the local LLM was "
            "unavailable). Start Ollama and re-run for fully written notes.",
            "",
        ]
    return "\n".join(lines)


def _group_chapters(chapters: list[Chapter], cfg) -> list[list[Chapter]]:
    """Split chapters into consecutive groups so we write a few long files."""
    if len(chapters) <= max(1, cfg.single_file_max_chapters):
        return [list(chapters)]
    size = max(1, cfg.chapters_per_file)
    return [chapters[i : i + size] for i in range(0, len(chapters), size)]


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
    """Generate written study notes for ``report`` into ``output_dir``.

    Topics are written individually (LLM or offline fallback) and then grouped
    into a few long Markdown files plus a ``README.md`` study-guide index.
    """
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
    informative_kinds = set(settings.analysis.visual_informative_kinds)

    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    doc_title = Path(report.source_path).stem or "Study notes"

    # 1) Group topics into a handful of long files up front so progress reports
    #    files, not raw topic/segment counts.
    groups = _group_chapters(chapters, settings.notes)
    single_group = len(groups) == 1
    num_files = len(groups)
    total_topics = len(chapters)
    bus.stage_start(
        Stage.NOTES,
        f"Writing study notes across {num_files} file(s)…",
        files=num_files,
    )

    files: list[Path] = []
    index_entries: list[tuple[str, str, list[str]]] = []
    processed = 0
    for part, group in enumerate(groups, start=1):
        items: list[tuple[Chapter, dict]] = []
        for chapter in group:
            title = chapter.title or f"Topic {processed + 1}"
            bus.progress(
                Stage.NOTES,
                processed / max(1, total_topics),
                f"Writing file {part} of {num_files} · {title}",
            )
            text = _chapter_text(report, chapter, limit=limit)

            notes: dict | None = None
            if use_llm and text:
                try:
                    notes = _coerce(
                        _llm_notes(client, settings.llm.model, title, text), title
                    )
                    used_llm_any = True
                except Exception as exc:  # noqa: BLE001 - degrade to reflowed notes
                    logger.warning("LLM notes failed for topic %d: %s", processed, exc)
                    notes = None
            if notes is None:
                notes = _coerce(_fallback_notes(title, text, chapter), title)
            notes["screenshots"] = _chapter_screenshots(
                report, chapter, informative_kinds, images_dir
            )
            items.append((chapter, notes))
            processed += 1

        topic_titles = [notes["title"] for _, notes in items]
        if single_group:
            group_title = doc_title
        elif len(items) == 1:
            group_title = f"Part {part}: {topic_titles[0]}"
        else:
            group_title = f"Part {part}: {topic_titles[0]} → {topic_titles[-1]}"

        slug_seed = doc_title if single_group else topic_titles[0]
        filename = f"{part:02d}-{_slug(slug_seed)}.md"
        (output_dir / filename).write_text(
            _render_group_md(group_title, items), encoding="utf-8"
        )
        files.append(output_dir / filename)
        index_entries.append((filename, group_title, topic_titles))
        bus.progress(
            Stage.NOTES,
            processed / max(1, total_topics),
            f"Finished file {part} of {num_files}",
        )

    index = output_dir / "README.md"
    index.write_text(
        _render_index_md(report, index_entries, used_llm=used_llm_any), encoding="utf-8"
    )
    files.insert(0, index)

    how = "with the local LLM" if used_llm_any else "reflowed from the analysis"
    message = f"Wrote {len(files)} Markdown file(s) {how}."
    bus.stage_end(Stage.NOTES, message, output=str(output_dir))
    logger.info("Notes written to %s (%s)", output_dir, message)

    return NotesResult(
        output_dir=output_dir, files=files, message=message, used_llm=used_llm_any
    )
