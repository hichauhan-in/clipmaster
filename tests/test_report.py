"""Tests for the Markdown report renderer."""

from clipmaster.models import (
    AnalysisReport,
    Chapter,
    ChunkPlan,
    ClipCandidate,
    MediaInfo,
    Transcript,
    VideoStreamInfo,
)
from clipmaster.report.builder import format_timestamp, render_markdown


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(3661) == "1:01:01"


def test_render_markdown_contains_sections():
    report = AnalysisReport(
        project_id="demo-1234abcd",
        source_path="/videos/demo.mp4",
        media=MediaInfo(
            path="/videos/demo.mp4",
            duration_s=600.0,
            size_bytes=2_000_000,
            video=VideoStreamInfo(codec="h264", width=1920, height=1080, fps=30.0),
        ),
        chunk_plan=ChunkPlan(
            total_duration_s=600.0, max_chunk_seconds=1200.0, overlap_seconds=2.0
        ),
        transcript=Transcript(language="en", duration_s=600.0),
        summary="An educational video about building pipelines.",
        keywords=["pipeline", "ffmpeg"],
        chapters=[Chapter(title="Intro", start=0, end=30, summary="Welcome")],
        clip_candidates=[
            ClipCandidate(title="Hook", start=10, end=25, score=0.9, hook="Watch this")
        ],
    )
    md = render_markdown(report)
    assert "# Analysis" in md
    assert "## Summary" in md
    assert "## Chapters" in md
    assert "## Suggested Clips" in md
    assert "pipeline" in md
    assert "Intro" in md
