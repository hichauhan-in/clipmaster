"""Pydantic data models — the shared vocabulary of the whole pipeline.

These models are intentionally serialisable (``model_dump_json``) because the
analysis artifact they describe is written to disk as ``analysis.json`` and later
consumed by the cleanup, shorts and editing features as well as the desktop UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Media description -------------------------------------------------------
class VideoStreamInfo(BaseModel):
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate: int | None = None


class AudioStreamInfo(BaseModel):
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    language: str | None = None


class MediaInfo(BaseModel):
    """Everything ffprobe can tell us about the input file."""

    path: str
    container: str | None = None
    duration_s: float = 0.0
    size_bytes: int = 0
    video: VideoStreamInfo | None = None
    audios: list[AudioStreamInfo] = Field(default_factory=list)

    @property
    def has_audio(self) -> bool:
        return len(self.audios) > 0


# --- Chunk planning ----------------------------------------------------------
class Chunk(BaseModel):
    index: int
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class ChunkPlan(BaseModel):
    total_duration_s: float
    max_chunk_seconds: float
    overlap_seconds: float
    chunks: list[Chunk] = Field(default_factory=list)


# --- Transcript --------------------------------------------------------------
class Word(BaseModel):
    text: str
    start: float
    end: float
    probability: float | None = None


class TranscriptSegment(BaseModel):
    """A contiguous span of speech with an absolute (whole-video) timeline."""

    id: int
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    chunk_index: int | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class Transcript(BaseModel):
    language: str | None = None
    duration_s: float = 0.0
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return " ".join(seg.text.strip() for seg in self.segments).strip()


# --- Silence -----------------------------------------------------------------
class SilenceSpan(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


# --- Analysis ----------------------------------------------------------------
class SegmentKind(str, Enum):
    ON_TOPIC = "on_topic"
    OFF_TOPIC = "off_topic"
    QA = "qa"
    FILLER = "filler"
    INTRO = "intro"
    OUTRO = "outro"
    TRANSITION = "transition"


class SegmentAnalysis(BaseModel):
    """Per-segment verdict that drives cleanup and clip selection."""

    segment_id: int
    start: float
    end: float
    kind: SegmentKind = SegmentKind.ON_TOPIC
    topic: str | None = None
    importance: float = 0.5  # 0..1, higher = more worth keeping
    keep: bool = True
    reason: str = ""


class Chapter(BaseModel):
    """A coherent topical section of the video."""

    title: str
    start: float
    end: float
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    segment_ids: list[int] = Field(default_factory=list)


class ClipCandidate(BaseModel):
    """A self-contained span that would make a good short."""

    title: str
    start: float
    end: float
    score: float = 0.5  # 0..1 shareability / standalone quality
    hook: str = ""       # one-line opening hook for the short
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class KeepSpan(BaseModel):
    """A span to retain in the cleaned-up cut (the cleanup EDL)."""

    start: float
    end: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class AnalysisReport(BaseModel):
    """The complete analysis artifact — the foundation for every action."""

    schema_version: int = 1
    project_id: str
    source_path: str
    created_at: str = Field(default_factory=_utcnow)

    media: MediaInfo
    chunk_plan: ChunkPlan
    transcript: Transcript
    silences: list[SilenceSpan] = Field(default_factory=list)

    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    segment_analyses: list[SegmentAnalysis] = Field(default_factory=list)
    clip_candidates: list[ClipCandidate] = Field(default_factory=list)
    cleanup_keep_spans: list[KeepSpan] = Field(default_factory=list)

    # Provenance so results are reproducible / debuggable.
    transcription_model: str = ""
    llm_model: str = ""
    warnings: list[str] = Field(default_factory=list)

    # --- Convenience metrics --------------------------------------------------
    @property
    def cleanup_kept_seconds(self) -> float:
        return sum(span.duration for span in self.cleanup_keep_spans)

    @property
    def cleanup_removed_seconds(self) -> float:
        return max(0.0, self.media.duration_s - self.cleanup_kept_seconds)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "AnalysisReport":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
