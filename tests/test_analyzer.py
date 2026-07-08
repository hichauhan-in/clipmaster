"""Tests for the deterministic analysis heuristics and response parsing."""

from clipmaster.analysis.ollama_client import OllamaClient
from clipmaster.analysis.transcript_analyzer import (
    _base_importance,
    _build_keep_spans,
    _filler_ratio,
    _parse_window_response,
    _window_segments,
)
from clipmaster.models import SegmentAnalysis, TranscriptSegment


FILLERS = ["um", "uh", "you know", "like", "basically"]


def test_filler_ratio_counts_single_and_multiword():
    assert _filler_ratio("um uh basically", FILLERS) == 1.0
    ratio = _filler_ratio("today we build a real pipeline", FILLERS)
    assert ratio == 0.0
    partial = _filler_ratio("you know we build things", FILLERS)
    assert 0.0 < partial < 1.0


def test_base_importance_penalises_filler_and_silence():
    seg = TranscriptSegment(id=0, start=0, end=5, text="today we build a pipeline")
    clean = _base_importance(seg, filler_ratio=0.0, silence_frac=0.0)
    filler = _base_importance(seg, filler_ratio=1.0, silence_frac=0.0)
    silent = _base_importance(seg, filler_ratio=0.0, silence_frac=1.0)
    assert clean > filler
    assert clean > silent
    assert 0.0 <= filler <= 1.0


def test_window_segments_respects_char_budget():
    segs = [
        TranscriptSegment(id=i, start=i, end=i + 1, text="word " * 20) for i in range(10)
    ]
    windows = _window_segments(segs, max_chars=200)
    assert len(windows) > 1
    # Every segment appears exactly once across windows.
    flat = [s.id for w in windows for s in w]
    assert sorted(flat) == list(range(10))


def test_build_keep_spans_merges_adjacent():
    analyses = [
        SegmentAnalysis(segment_id=0, start=0.0, end=5.0, keep=True),
        SegmentAnalysis(segment_id=1, start=5.2, end=9.0, keep=True),  # small gap -> merge
        SegmentAnalysis(segment_id=2, start=20.0, end=25.0, keep=False),  # dropped
        SegmentAnalysis(segment_id=3, start=30.0, end=33.0, keep=True),
    ]
    spans = _build_keep_spans(analyses, gap_tolerance=0.75)
    assert len(spans) == 2
    assert spans[0].start == 0.0 and spans[0].end == 9.0
    assert spans[1].start == 30.0 and spans[1].end == 33.0


def test_parse_window_response_handles_partial_json():
    data = {
        "summary": "A section about pipelines.",
        "keywords": ["pipeline", "ffmpeg"],
        "chapters": [
            {"title": "Intro", "start": 0, "end": 30, "summary": "s", "keywords": []},
            {"title": "bad", "start": 40, "end": 30},  # invalid (end<=start) -> skipped
        ],
        "clips": [{"title": "Highlight", "start": 10, "end": 25, "score": 1.5}],
        "off_topic_spans": [{"start": 50, "end": 60, "reason": "aside"}],
        "qa_spans": "not a list",
    }
    result = _parse_window_response(data)
    assert result.summary.startswith("A section")
    assert result.keywords == ["pipeline", "ffmpeg"]
    assert len(result.chapters) == 1
    assert result.clips[0].score == 1.0  # clamped to 0..1
    assert result.off_topic == [(50.0, 60.0, "aside")]
    assert result.qa == []


def test_ollama_json_extraction_from_noisy_text():
    raw = 'Sure! Here is the result:\n{"summary": "ok", "keywords": []}\nThanks.'
    parsed = OllamaClient._parse_json(raw)
    assert parsed["summary"] == "ok"
