"""Tests for the data models: serialisation and derived metrics."""

from clipmaster.models import (
    AnalysisReport,
    Chapter,
    ChunkPlan,
    KeepSpan,
    MediaInfo,
    Transcript,
    TranscriptSegment,
    VideoStreamInfo,
)


def _sample_report() -> AnalysisReport:
    return AnalysisReport(
        project_id="demo-1234abcd",
        source_path="/videos/demo.mp4",
        media=MediaInfo(
            path="/videos/demo.mp4",
            duration_s=600.0,
            size_bytes=1024,
            video=VideoStreamInfo(codec="h264", width=1920, height=1080, fps=30.0),
        ),
        chunk_plan=ChunkPlan(
            total_duration_s=600.0, max_chunk_seconds=1200.0, overlap_seconds=2.0
        ),
        transcript=Transcript(
            language="en",
            duration_s=600.0,
            segments=[
                TranscriptSegment(id=0, start=0.0, end=5.0, text="hello world"),
            ],
        ),
        cleanup_keep_spans=[KeepSpan(start=0.0, end=450.0)],
    )


def test_report_roundtrip(tmp_path):
    report = _sample_report()
    path = report.save(tmp_path / "analysis.json")
    loaded = AnalysisReport.load(path)
    assert loaded.project_id == report.project_id
    assert loaded.media.duration_s == 600.0
    assert loaded.transcript.segments[0].text == "hello world"


def test_cleanup_metrics():
    report = _sample_report()
    assert report.cleanup_kept_seconds == 450.0
    assert report.cleanup_removed_seconds == 150.0


def test_transcript_full_text():
    t = Transcript(
        segments=[
            TranscriptSegment(id=0, start=0, end=1, text="  Hello "),
            TranscriptSegment(id=1, start=1, end=2, text="world  "),
        ]
    )
    assert t.full_text == "Hello world"


def test_chapter_duration_and_clip_helpers():
    ch = Chapter(title="Intro", start=0.0, end=30.0)
    assert ch.end - ch.start == 30.0
