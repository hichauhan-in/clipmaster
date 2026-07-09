"""Turn a transcript into a rich, grounded analysis.

The analyzer combines two complementary signals:

* **Heuristics** (cheap, deterministic): filler-word density, silence overlap,
  segment length and speech confidence give every segment a base *importance*.
* **LLM understanding** (local Ollama): topical chapters, off-topic / Q&A spans,
  a summary, keywords, and standalone clip candidates.

If Ollama is unavailable the analyzer *degrades gracefully* to heuristics only and
records a warning, so the pipeline still produces a usable report. Everything is
expressed on the video's absolute timeline so downstream features (cleanup,
shorts, editing) can act on the numbers directly.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from clipmaster.analysis.ollama_client import OllamaClient, OllamaError
from clipmaster.config import AnalysisConfig, LLMConfig, SignalWeights
from clipmaster.logging_setup import get_logger
from clipmaster.models import (
    AudioFeatures,
    Chapter,
    ClipCandidate,
    KeepSpan,
    SegmentAnalysis,
    SegmentAudio,
    SegmentKind,
    SilenceSpan,
    Transcript,
    TranscriptSegment,
    VisualFeatures,
    VisualKeyframe,
)

logger = get_logger("analysis.transcript")

_WORD_RE = re.compile(r"[a-z']+")

# Upper bound on clip candidates surfaced by the analyzer; the shorts feature
# applies the final duration/count selection from the clips config.
_MAX_CLIP_CANDIDATES = 12

# Sign-off / outro cues. When one of these appears in the latter part of a video
# ("that's it for this video", "thanks for watching", "see you in the next one"),
# everything from there to the end is the outro — typically a playlist / subscribe
# / course self-promotion wrap-up — and is dropped from the cleaned cut.
_SIGN_OFF_RE = re.compile(
    r"(that'?s it for (this|the|today'?s|our|my)?\s*(video|lesson|tutorial|session|episode|one)"
    r"|that'?s all (for (this|today|now)|i have|for me)"
    r"|thank(s| you)( so much| very much)? for watching"
    r"|that (wraps|brings) (up|it up|things up|us to the end)"
    r"|that (concludes|is the end of|is it for)"
    r"|we'?ve (come to|reached) the end"
    r"|see you (in the next|next time|guys)"
    r"|catch you (in the next|next time|later)"
    r"|(until|till) (next time|the next (one|video|lesson)))",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "You are an expert video editor and content analyst. You analyse the "
    "transcript of an educational/work video and return STRICT JSON only. "
    "Always reuse the exact start/end timestamps given for the segments; never "
    "invent times outside the provided range."
)


@dataclass
class _WindowResult:
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    clips: list[ClipCandidate] = field(default_factory=list)
    off_topic: list[tuple[float, float, str]] = field(default_factory=list)
    qa: list[tuple[float, float, str]] = field(default_factory=list)
    promo: list[tuple[float, float, str]] = field(default_factory=list)


# --- Heuristics --------------------------------------------------------------
def _filler_ratio(text: str, filler_words: list[str]) -> float:
    words = _WORD_RE.findall(text.lower())
    if not words:
        return 1.0  # empty/no-speech segment behaves like pure filler
    joined = " ".join(words)
    filler_hits = 0
    for filler in filler_words:
        if " " in filler:
            filler_hits += joined.count(filler)
        else:
            filler_hits += words.count(filler)
    return min(1.0, filler_hits / max(1, len(words)))


def _silence_overlap(seg: TranscriptSegment, silences: list[SilenceSpan]) -> float:
    """Fraction of the segment covered by detected silence."""
    if seg.duration <= 0:
        return 0.0
    covered = 0.0
    for s in silences:
        overlap = min(seg.end, s.end) - max(seg.start, s.start)
        if overlap > 0:
            covered += overlap
    return min(1.0, covered / seg.duration)


def _base_importance(
    seg: TranscriptSegment, filler_ratio: float, silence_frac: float
) -> float:
    """Deterministic 0..1 importance before LLM adjustment."""
    score = 0.6
    score -= 0.5 * filler_ratio
    score -= 0.4 * silence_frac
    if seg.duration < 1.5:
        score -= 0.1
    if seg.duration > 6.0:
        score += 0.1
    if seg.no_speech_prob is not None and seg.no_speech_prob > 0.6:
        score -= 0.2
    n_words = len(_WORD_RE.findall(seg.text.lower()))
    if n_words < 3:
        score -= 0.1
    return max(0.0, min(1.0, score))


# --- LLM windowing -----------------------------------------------------------
def _window_segments(
    segments: list[TranscriptSegment], max_chars: int
) -> list[list[TranscriptSegment]]:
    """Group segments into windows whose rendered text stays under ``max_chars``."""
    windows: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    size = 0
    for seg in segments:
        seg_len = len(seg.text) + 24  # +overhead for the id/timestamp prefix
        if current and size + seg_len > max_chars:
            windows.append(current)
            current = []
            size = 0
        current.append(seg)
        size += seg_len
    if current:
        windows.append(current)
    return windows


def _render_window(segments: list[TranscriptSegment]) -> str:
    return "\n".join(
        f"[{seg.id}] {seg.start:.1f}-{seg.end:.1f}: {seg.text}" for seg in segments
    )


def _window_prompt(segments: list[TranscriptSegment]) -> str:
    lo = segments[0].start
    hi = segments[-1].end
    return (
        f"Transcript segments (time range {lo:.1f}s to {hi:.1f}s):\n"
        f"{_render_window(segments)}\n\n"
        "Return JSON with EXACTLY these keys:\n"
        '{\n'
        '  "summary": "2-3 sentence summary of THIS section",\n'
        '  "keywords": ["5-10 topical keywords"],\n'
        '  "chapters": [{"title": str, "start": float, "end": float,\n'
        '                "summary": str, "keywords": [str]}],\n'
        '  "off_topic_spans": [{"start": float, "end": float, "reason": str}],\n'
        '  "qa_spans": [{"start": float, "end": float, "reason": str}],\n'
        '  "promo_spans": [{"start": float, "end": float, "reason": str}],\n'
        '  "clips": [{"title": str, "start": float, "end": float,\n'
        '             "hook": str, "score": float, "reason": str}]\n'
        "}\n"
        "Rules: chapters must tile the section without gaps; off_topic_spans are "
        "tangents/asides not core to the topic; qa_spans are audience Q&A not "
        "essential to the main content; promo_spans are self-promotion or "
        "advertising that is NOT part of the educational content — e.g. plugging "
        "the author's own courses/products/services, sponsor reads, asking viewers "
        "to like/subscribe/follow, discount or coupon codes, 'link in the "
        "description/bio', merch or Patreon shout-outs, and end-of-video wrap-ups "
        "like 'that's it for this video', 'check out my other videos / playlist', "
        "'see you in the next one'. Once the creator begins signing off near the "
        "end, mark the ENTIRE remainder through the last timestamp as promo. These "
        "often appear at the very start or end. Mark them even if a slide is shown "
        "on screen. clips "
        "are self-contained highlights 8-40s long with score 0..1. Use only "
        "timestamps within the given range."
    )


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_spans(items: object) -> list[tuple[float, float, str]]:
    spans: list[tuple[float, float, str]] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                start = _coerce_float(it.get("start"))
                end = _coerce_float(it.get("end"))
                if end > start:
                    spans.append((start, end, str(it.get("reason", ""))))
    return spans


def _parse_window_response(data: object) -> _WindowResult:
    result = _WindowResult()
    if not isinstance(data, dict):
        return result

    result.summary = str(data.get("summary", "")).strip()
    kw = data.get("keywords")
    if isinstance(kw, list):
        result.keywords = [str(k).strip() for k in kw if str(k).strip()]

    for ch in data.get("chapters", []) or []:
        if not isinstance(ch, dict):
            continue
        start = _coerce_float(ch.get("start"))
        end = _coerce_float(ch.get("end"))
        if end <= start:
            continue
        ch_kw = ch.get("keywords")
        result.chapters.append(
            Chapter(
                title=str(ch.get("title", "Untitled")).strip() or "Untitled",
                start=start,
                end=end,
                summary=str(ch.get("summary", "")).strip(),
                keywords=[str(k) for k in ch_kw] if isinstance(ch_kw, list) else [],
            )
        )

    for cl in data.get("clips", []) or []:
        if not isinstance(cl, dict):
            continue
        start = _coerce_float(cl.get("start"))
        end = _coerce_float(cl.get("end"))
        if end <= start:
            continue
        result.clips.append(
            ClipCandidate(
                title=str(cl.get("title", "Clip")).strip() or "Clip",
                start=start,
                end=end,
                score=max(0.0, min(1.0, _coerce_float(cl.get("score"), 0.5))),
                hook=str(cl.get("hook", "")).strip(),
                reason=str(cl.get("reason", "")).strip(),
            )
        )

    result.off_topic = _coerce_spans(data.get("off_topic_spans"))
    result.qa = _coerce_spans(data.get("qa_spans"))
    result.promo = _coerce_spans(data.get("promo_spans"))
    return result


class TranscriptAnalyzer:
    """Analyse a :class:`Transcript` into report-ready structures."""

    def __init__(self, llm_config: LLMConfig, analysis_config: AnalysisConfig) -> None:
        self.llm_config = llm_config
        self.analysis_config = analysis_config
        self.client = OllamaClient(
            host=llm_config.host,
            model=llm_config.model,
            temperature=llm_config.temperature,
            timeout=llm_config.request_timeout_seconds,
        )

    # --- Public API -----------------------------------------------------------
    def analyze(
        self,
        transcript: Transcript,
        silences: list[SilenceSpan],
        *,
        progress=None,
        audio_features: AudioFeatures | None = None,
        visual_features: VisualFeatures | None = None,
    ) -> dict:
        """Return a dict of analysis fields ready to attach to an AnalysisReport."""
        warnings: list[str] = []
        segments = transcript.segments

        # Index the complementary signals for quick per-segment lookup.
        audio_by_id: dict[int, SegmentAudio] = {}
        if audio_features is not None:
            audio_by_id = {a.segment_id: a for a in audio_features.segments}
        keyframes: list[VisualKeyframe] = []
        if visual_features is not None:
            keyframes = sorted(visual_features.keyframes, key=lambda k: k.time)
        kf_times = [k.time for k in keyframes]

        # 1) Heuristic base scores for every segment.
        filler_words = self.analysis_config.filler_words
        heur: dict[int, tuple[float, float]] = {}
        for seg in segments:
            fr = _filler_ratio(seg.text, filler_words)
            sf = _silence_overlap(seg, silences)
            heur[seg.id] = (_base_importance(seg, fr, sf), fr)

        # 2) LLM understanding (graceful fallback to heuristics).
        windows_out: list[_WindowResult] = []
        llm_ok = self.client.is_available()
        if not llm_ok:
            warnings.append(
                f"Ollama unavailable at {self.llm_config.host}; used heuristic analysis only."
            )
            logger.warning(warnings[-1])
        else:
            windows = _window_segments(segments, self.llm_config.max_input_chars)
            for i, window in enumerate(windows):
                if progress is not None:
                    progress(i / max(1, len(windows)), f"Analyzing window {i + 1}/{len(windows)}")
                try:
                    data = self.client.chat_json(
                        _window_prompt(window), system=_SYSTEM_PROMPT
                    )
                    windows_out.append(_parse_window_response(data))
                except OllamaError as exc:
                    warnings.append(f"LLM window {i + 1} failed: {exc}")
                    logger.warning(warnings[-1])

        # 3) Merge LLM outputs.
        chapters: list[Chapter] = []
        clips: list[ClipCandidate] = []
        keywords: list[str] = []
        off_topic_spans: list[tuple[float, float, str]] = []
        qa_spans: list[tuple[float, float, str]] = []
        promo_spans: list[tuple[float, float, str]] = []
        summaries: list[str] = []
        for w in windows_out:
            chapters.extend(w.chapters)
            clips.extend(w.clips)
            keywords.extend(w.keywords)
            off_topic_spans.extend(w.off_topic)
            qa_spans.extend(w.qa)
            promo_spans.extend(w.promo)
            if w.summary:
                summaries.append(w.summary)

        chapters.sort(key=lambda c: c.start)
        clips.sort(key=lambda c: c.score, reverse=True)
        keywords = _dedupe_keep_order(keywords)

        # 4) Global summary.
        summary = self._global_summary(summaries) if summaries else ""

        # 5) Per-segment verdicts fusing transcript + audio + visual signals.
        segment_analyses = self._build_segment_analyses(
            segments,
            heur,
            off_topic_spans,
            qa_spans,
            promo_spans,
            chapters,
            audio_by_id,
            keyframes,
            kf_times,
        )

        # 6) Attach segment ids to chapters and keep a generous pool of clips
        #    (the shorts feature applies the final duration/count selection).
        _assign_segments_to_chapters(chapters, segments)
        _boost_clips(clips, segments, audio_by_id, keyframes)
        clips = clips[:_MAX_CLIP_CANDIDATES]

        # 7) Cleanup keep-spans from kept segments, plus silent-but-visually
        #    active footage (navigation / on-screen demos) so relevant sections
        #    are never trimmed just because nobody is talking over them.
        keep_spans = _build_keep_spans(
            segment_analyses,
            visual_features=visual_features,
            informative_kinds=set(self.analysis_config.visual_informative_kinds),
            visual_pad=self.analysis_config.visual_keep_pad_seconds,
        )

        return {
            "summary": summary,
            "keywords": keywords[:25],
            "chapters": chapters,
            "segment_analyses": segment_analyses,
            "clip_candidates": clips,
            "cleanup_keep_spans": keep_spans,
            "warnings": warnings,
        }

    def _global_summary(self, summaries: list[str]) -> str:
        joined = " ".join(summaries)
        if not self.client.is_available():
            return joined[:1200]
        try:
            prompt = (
                "Combine these section summaries of one video into a single cohesive "
                "3-5 sentence overview. Return JSON: {\"summary\": str}.\n\n" + joined
            )
            data = self.client.chat_json(prompt, system=_SYSTEM_PROMPT)
            if isinstance(data, dict) and data.get("summary"):
                return str(data["summary"]).strip()
        except OllamaError:
            pass
        return joined[:1200]

    def _build_segment_analyses(
        self,
        segments: list[TranscriptSegment],
        heur: dict[int, tuple[float, float]],
        off_topic_spans: list[tuple[float, float, str]],
        qa_spans: list[tuple[float, float, str]],
        promo_spans: list[tuple[float, float, str]],
        chapters: list[Chapter],
        audio_by_id: dict[int, SegmentAudio],
        keyframes: list[VisualKeyframe],
        kf_times: list[float],
    ) -> list[SegmentAnalysis]:
        ac = self.analysis_config
        weights = ac.weights
        threshold = ac.keep_importance_threshold
        informative_kinds = set(ac.visual_informative_kinds)
        floor = ac.visual_floor_importance
        promo_phrases = [p.lower() for p in ac.promo_phrases] if ac.remove_promotional else []

        # Sign-off / outro cutoff: once the creator wraps up in the latter part of
        # the video ("that's it for the video", "thanks for watching"), everything
        # from there to the end is outro self-promotion (playlist plugs, subscribe
        # CTAs) and is dropped — even if a slide is still on screen.
        outro_start: float | None = None
        if ac.remove_promotional:
            video_end = max((s.end for s in segments), default=0.0)
            if video_end > 0:
                for seg in segments:
                    if seg.start >= 0.6 * video_end and _SIGN_OFF_RE.search(seg.text):
                        outro_start = seg.start
                        break

        analyses: list[SegmentAnalysis] = []
        for seg in segments:
            transcript_importance, filler_ratio = heur[seg.id]
            kind = SegmentKind.ON_TOPIC
            reason = ""

            # Transcript-derived verdict (caps importance for weak spans).
            if filler_ratio >= 0.6:
                kind = SegmentKind.FILLER
                transcript_importance = min(transcript_importance, 0.2)
                reason = "High filler / low information density."
            if _in_spans(seg, qa_spans):
                kind = SegmentKind.QA
                transcript_importance = min(transcript_importance, 0.4)
                reason = reason or "Audience Q&A, tangential to core topic."
            if _in_spans(seg, off_topic_spans):
                kind = SegmentKind.OFF_TOPIC
                transcript_importance = min(transcript_importance, 0.3)
                reason = reason or "Off-topic aside."

            # Self-promotion / advertising: flagged by the LLM or matched by an
            # obvious promo phrase. Promo is always dropped and (below) is never
            # rescued by on-screen value — a course-advert slide is still an advert.
            is_promo = ac.remove_promotional and (
                _in_spans(seg, promo_spans)
                or _phrase_hit(seg.text, promo_phrases)
                or (outro_start is not None and seg.start >= outro_start)
            )
            if is_promo:
                kind = SegmentKind.PROMO
                transcript_importance = min(transcript_importance, 0.1)
                reason = "Self-promotion / advertising, not part of the content."

            # Complementary signals.
            audio = audio_by_id.get(seg.id)
            audio_score = audio.energy_score if audio is not None else None
            kf = _nearest_keyframe(keyframes, kf_times, (seg.start + seg.end) / 2)
            visual_score = kf.informativeness if kf is not None else None
            visual_kind = kf.kind.value if kf is not None else None

            importance = _fuse(weights, transcript_importance, audio_score, visual_score)

            # Visual floor: on-screen teaching content (slides, demos, code, labs,
            # diagrams) is never dropped just because speech is sparse — except for
            # promotional segments, which stay dropped even with a slide on screen.
            visual_informative = (
                kf is not None
                and visual_kind in informative_kinds
                and visual_score is not None
                and visual_score >= 0.4
            )
            if visual_informative and not is_promo:
                importance = max(importance, floor)
                if kind in {SegmentKind.FILLER, SegmentKind.OFF_TOPIC}:
                    kind = SegmentKind.ON_TOPIC
                reason = (
                    f"On-screen {visual_kind.replace('_', ' ')} content — "
                    "kept for visual value."
                )

            topic = _chapter_title_at(chapters, seg.start)
            keep = (not is_promo) and (
                (visual_informative)
                or (
                    importance >= threshold
                    and kind not in {SegmentKind.FILLER, SegmentKind.OFF_TOPIC}
                )
            )
            analyses.append(
                SegmentAnalysis(
                    segment_id=seg.id,
                    start=seg.start,
                    end=seg.end,
                    kind=kind,
                    topic=topic,
                    importance=round(importance, 3),
                    keep=keep,
                    reason=reason,
                    transcript_importance=round(transcript_importance, 3),
                    audio_score=round(audio_score, 3) if audio_score is not None else None,
                    visual_score=round(visual_score, 3) if visual_score is not None else None,
                    visual_kind=visual_kind,
                )
            )
        return analyses


# --- Module helpers ----------------------------------------------------------
def _fuse(
    weights: SignalWeights,
    transcript_score: float,
    audio_score: float | None,
    visual_score: float | None,
) -> float:
    """Weighted mean of the available signals (weights renormalised on absence)."""
    parts: list[tuple[float, float]] = [(max(0.0, weights.transcript), transcript_score)]
    if audio_score is not None:
        parts.append((max(0.0, weights.audio), audio_score))
    if visual_score is not None:
        parts.append((max(0.0, weights.visual), visual_score))
    total = sum(w for w, _ in parts)
    if total <= 0:
        return transcript_score
    return sum(w * v for w, v in parts) / total


def _nearest_keyframe(
    keyframes: list[VisualKeyframe], times: list[float], t: float
) -> VisualKeyframe | None:
    """The sampled keyframe closest in time to ``t`` (sparse frames -> nearest)."""
    if not keyframes:
        return None
    i = bisect.bisect_left(times, t)
    if i <= 0:
        return keyframes[0]
    if i >= len(times):
        return keyframes[-1]
    before, after = times[i - 1], times[i]
    return keyframes[i - 1] if (t - before) <= (after - t) else keyframes[i]


def _boost_clips(
    clips: list[ClipCandidate],
    segments: list[TranscriptSegment],
    audio_by_id: dict[int, SegmentAudio],
    keyframes: list[VisualKeyframe],
) -> None:
    """Nudge clip scores up for spans with strong audio energy or on-screen value."""
    if not clips or (not audio_by_id and not keyframes):
        return
    for clip in clips:
        energies = [
            audio_by_id[s.id].energy_score
            for s in segments
            if s.id in audio_by_id and s.start < clip.end and s.end > clip.start
        ]
        infos = [kf.informativeness for kf in keyframes if clip.start <= kf.time <= clip.end]
        boost = 0.0
        if energies:
            boost += 0.10 * max(0.0, max(energies) - 0.5)
        if infos:
            boost += 0.18 * max(0.0, max(infos) - 0.5)
        if boost:
            clip.score = round(max(0.0, min(1.0, clip.score + boost)), 3)
    clips.sort(key=lambda c: c.score, reverse=True)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _in_spans(seg: TranscriptSegment, spans: list[tuple[float, float, str]]) -> bool:
    mid = (seg.start + seg.end) / 2
    return any(start <= mid <= end for start, end, _ in spans)


def _phrase_hit(text: str, phrases: list[str]) -> bool:
    """True when the segment text contains any promotional phrase (case-insensitive)."""
    if not phrases:
        return False
    low = text.lower()
    return any(p in low for p in phrases)


def _chapter_title_at(chapters: list[Chapter], t: float) -> str | None:
    for ch in chapters:
        if ch.start <= t <= ch.end:
            return ch.title
    return None


def _assign_segments_to_chapters(
    chapters: list[Chapter], segments: list[TranscriptSegment]
) -> None:
    for ch in chapters:
        ch.segment_ids = [
            seg.id for seg in segments if ch.start <= seg.start < ch.end
        ]


def _build_keep_spans(
    analyses: list[SegmentAnalysis],
    *,
    visual_features: VisualFeatures | None = None,
    informative_kinds: set[str] | None = None,
    min_informativeness: float = 0.4,
    visual_pad: float = 2.0,
    gap_tolerance: float = 0.75,
) -> list[KeepSpan]:
    """Build cleanup keep-spans.

    First merges consecutive kept transcript segments. Then adds *visual* keep
    spans for silent stretches where the screen is doing something meaningful —
    on-screen navigation, demos, code, slides — so footage that shows *how to
    reach* something is preserved even with no narration over it. Scene changes
    around each informative keyframe are used to keep a navigation sequence
    contiguous instead of a series of fragments.
    """
    spans: list[KeepSpan] = []
    for a in analyses:
        if not a.keep:
            continue
        if spans and a.start - spans[-1].end <= gap_tolerance:
            spans[-1].end = a.end
        else:
            spans.append(KeepSpan(start=a.start, end=a.end, reason="kept"))

    # Promotional segments are dropped even if a slide is on screen, so exclude
    # their time ranges from the visual keep-spans below (a course-advert slide
    # must not sneak the advert back into the cut).
    promo_ranges = [
        (a.start, a.end) for a in analyses if a.kind == SegmentKind.PROMO
    ]

    visual_spans = _visual_keep_spans(
        visual_features, informative_kinds or set(), min_informativeness, visual_pad
    )
    visual_spans = _subtract_ranges(visual_spans, promo_ranges)
    if not visual_spans:
        return spans
    return _merge_keep_spans(spans + visual_spans, gap_tolerance)


def _subtract_ranges(
    spans: list[KeepSpan], exclude: list[tuple[float, float]]
) -> list[KeepSpan]:
    """Remove ``exclude`` intervals from ``spans`` (interval difference)."""
    if not exclude:
        return spans
    cuts = sorted((s, e) for s, e in exclude if e > s)
    out: list[KeepSpan] = []
    for span in spans:
        pieces = [(span.start, span.end)]
        for cs, ce in cuts:
            next_pieces: list[tuple[float, float]] = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:  # no overlap
                    next_pieces.append((ps, pe))
                    continue
                if cs > ps:
                    next_pieces.append((ps, cs))
                if ce < pe:
                    next_pieces.append((ce, pe))
            pieces = next_pieces
        for ps, pe in pieces:
            if pe - ps > 0.2:  # drop slivers
                out.append(KeepSpan(start=ps, end=pe, reason=span.reason))
    return out


def _visual_keep_spans(
    visual_features: VisualFeatures | None,
    informative_kinds: set[str],
    min_informativeness: float,
    pad: float,
) -> list[KeepSpan]:
    """Keep-spans for on-screen activity that has no narration over it."""
    if visual_features is None or not visual_features.keyframes or not informative_kinds:
        return []
    scene_changes = sorted(visual_features.scene_changes)
    raw: list[KeepSpan] = []
    for kf in sorted(visual_features.keyframes, key=lambda k: k.time):
        if kf.kind.value not in informative_kinds:
            continue
        if kf.informativeness < min_informativeness:
            continue
        start = max(0.0, kf.time - pad)
        end = kf.time + pad
        # Stretch to the surrounding scene-change boundaries (if they are close)
        # so a continuous navigation sequence stays in one piece.
        prev_sc = _nearest_below(scene_changes, kf.time)
        next_sc = _nearest_above(scene_changes, kf.time)
        if prev_sc is not None and kf.time - prev_sc <= pad * 3:
            start = min(start, max(0.0, prev_sc - 0.25))
        if next_sc is not None and next_sc - kf.time <= pad * 3:
            end = max(end, next_sc + 0.25)
        label = kf.kind.value.replace("_", " ")
        raw.append(
            KeepSpan(start=start, end=end, reason=f"On-screen {label} (no narration)")
        )
    # Bridge nearby windows (dense scene-change navigation) into one span.
    return _merge_keep_spans(raw, gap_tolerance=max(pad * 2, 4.0))


def _merge_keep_spans(spans: list[KeepSpan], gap_tolerance: float) -> list[KeepSpan]:
    """Sort and coalesce overlapping / adjacent keep-spans (transcript wins)."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s.start)
    merged: list[KeepSpan] = [
        KeepSpan(start=ordered[0].start, end=ordered[0].end, reason=ordered[0].reason)
    ]
    for s in ordered[1:]:
        last = merged[-1]
        if s.start - last.end <= gap_tolerance:
            last.end = max(last.end, s.end)
            if last.reason != "kept" and s.reason == "kept":
                last.reason = "kept"
        else:
            merged.append(KeepSpan(start=s.start, end=s.end, reason=s.reason))
    return merged


def _nearest_below(values: list[float], t: float) -> float | None:
    """Largest value <= ``t`` from a sorted list, or None."""
    i = bisect.bisect_right(values, t)
    return values[i - 1] if i > 0 else None


def _nearest_above(values: list[float], t: float) -> float | None:
    """Smallest value >= ``t`` from a sorted list, or None."""
    i = bisect.bisect_left(values, t)
    return values[i] if i < len(values) else None


def analyze_transcript(
    transcript: Transcript,
    silences: list[SilenceSpan],
    llm_config: LLMConfig,
    analysis_config: AnalysisConfig,
    *,
    progress=None,
    audio_features: AudioFeatures | None = None,
    visual_features: VisualFeatures | None = None,
) -> dict:
    """Convenience wrapper around :class:`TranscriptAnalyzer`."""
    analyzer = TranscriptAnalyzer(llm_config, analysis_config)
    return analyzer.analyze(
        transcript,
        silences,
        progress=progress,
        audio_features=audio_features,
        visual_features=visual_features,
    )
