"""Request/response models for the HTTP API (kept separate from domain models)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Start an analysis job for a local video file."""

    path: str = Field(..., description="Absolute path to the input video.")
    skip_analysis: bool = Field(
        False, description="Transcript + silence only; skip the LLM analysis step."
    )


class JobRef(BaseModel):
    """Returned when a job is created."""

    job_id: str
    status: str


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | done | error
    project_id: str | None = None
    error: str | None = None
    event_count: int = 0


class ComponentStatus(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class HealthResponse(BaseModel):
    version: str
    workspace: str
    components: list[ComponentStatus]


class ProjectSummary(BaseModel):
    project_id: str
    source_path: str
    created_at: str
    duration_s: float
    chapters: int
    clips: int
    has_transcript: bool


class ProbeResponse(BaseModel):
    duration_s: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_streams: int = 0
    chunk_count: int = 0
