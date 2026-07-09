"""Request/response models for the HTTP API (kept separate from domain models)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignalWeightsInput(BaseModel):
    """Relative weight of each analysis signal (per-job override)."""

    transcript: float = 0.6
    audio: float = 0.2
    visual: float = 0.2


class AnalyzeRequest(BaseModel):
    """Start an analysis job for a local video file."""

    path: str = Field(..., description="Absolute path to the input video.")
    skip_analysis: bool = Field(
        False, description="Transcript + silence only; skip the LLM analysis step."
    )
    # Per-job overrides for the multi-factor analysis. ``None`` means "use the
    # value from the active configuration".
    audio_enabled: bool | None = Field(
        None, description="Include audio delivery (loudness/pace) as a signal."
    )
    visual_enabled: bool | None = Field(
        None, description="Include on-screen visual content (vision model) as a signal."
    )
    weights: SignalWeightsInput | None = Field(
        None, description="Balance between transcript/audio/visual signals."
    )


class JobRef(BaseModel):
    """Returned when a job is created."""

    job_id: str
    status: str


# --- Post-analysis actions (notes / cleanup / shorts) ------------------------
class NotesRequest(BaseModel):
    """Generate study notes and/or export the transcript for a project."""

    output_dir: str | None = Field(
        None,
        description="Parent folder to write the outputs into. Omit to use the project folder.",
    )
    notes: bool = Field(True, description="Generate Markdown study notes.")
    transcript: bool = Field(False, description="Export the verbatim transcript.")
    transcript_timestamps: bool = Field(
        True, description="Include [mm:ss] timestamps in the exported transcript."
    )


class CleanupRequest(BaseModel):
    """Render the cleaned-up cut for a project."""

    output_dir: str | None = Field(
        None, description="Folder to write the cleaned video into. Omit for the project folder."
    )


class ShortsRequest(BaseModel):
    """Render vertical short-form clips within a soft duration range."""

    min_seconds: float = Field(10.0, ge=3.0, le=180.0)
    max_seconds: float = Field(30.0, ge=3.0, le=180.0)
    count: int | None = Field(None, ge=1, le=30, description="How many shorts to render.")
    output_dir: str | None = Field(
        None, description="Folder to write the shorts into. Omit for the project folder."
    )


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


# --- Diagnostics tab ---------------------------------------------------------
class FixHint(BaseModel):
    """How to install/repair a missing dependency (shown as UI actions)."""

    winget: str = ""  # copy-paste winget command, if applicable
    url: str = ""  # official download/docs page
    hint: str = ""  # short human explanation


class DiagnosticsComponent(BaseModel):
    name: str
    category: str = "general"  # media | python | llm
    ok: bool
    detail: str = ""
    version: str | None = None
    fix: FixHint | None = None  # present only when the component needs attention


class OllamaModel(BaseModel):
    name: str
    size_bytes: int | None = None
    family: str | None = None
    parameter_size: str | None = None


class OllamaStatus(BaseModel):
    reachable: bool
    host: str
    port: int | None = None
    version: str | None = None
    models: list[OllamaModel] = Field(default_factory=list)
    selected_model: str
    selected_vision_model: str = ""
    error: str | None = None


class PythonInfo(BaseModel):
    version: str
    executable: str


class LogInfo(BaseModel):
    path: str | None = None
    level: str = "INFO"


class DiagnosticsResponse(BaseModel):
    version: str
    workspace: str
    python: PythonInfo
    components: list[DiagnosticsComponent]
    ollama: OllamaStatus
    log: LogInfo


class ActionResult(BaseModel):
    ok: bool
    message: str = ""


class SelectModelRequest(BaseModel):
    model: str


class PullRequest(BaseModel):
    model: str


class PullStatus(BaseModel):
    pull_id: str
    model: str
    status: str
    percent: float = 0.0
    message: str = ""
    done: bool = False
    error: str | None = None


class LogPathRequest(BaseModel):
    path: str


class LogsResponse(BaseModel):
    path: str | None = None
    level: str = "INFO"
    lines: list[str] = Field(default_factory=list)
