"""Typed configuration for ClipMaster.

Configuration is layered, later layers overriding earlier ones:

1. ``config/default.yaml`` shipped with the repo (safe defaults).
2. ``config/local.yaml`` if present (git-ignored, machine specific).
3. An explicit path passed on the CLI via ``--config``.
4. A handful of ``CLIPMASTER_*`` environment variables for quick overrides.

The merged mapping is validated into the :class:`Settings` model so the rest of
the codebase can rely on typed, documented attributes instead of loose dicts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Repository layout anchors ---------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"
LOCAL_CONFIG_PATH = REPO_ROOT / "config" / "local.yaml"


# --- Sub-models mirror the sections in config/default.yaml --------------------
class MediaConfig(BaseModel):
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"


class ChunkingConfig(BaseModel):
    max_chunk_seconds: float = 1200.0
    overlap_seconds: float = 2.0


class SilenceConfig(BaseModel):
    noise_db: float = -30.0
    min_silence_seconds: float = 0.6


class TranscriptionConfig(BaseModel):
    provider: str = "faster_whisper"
    model: str = "small"
    language: str | None = None
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    word_timestamps: bool = True


class LLMConfig(BaseModel):
    host: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    vision_model: str = "qwen2.5vl:7b"
    temperature: float = 0.2
    max_input_chars: int = 12000
    request_timeout_seconds: float = 300.0


class SignalWeights(BaseModel):
    """Relative weight of each analysis signal when fusing segment importance."""

    transcript: float = 0.6
    audio: float = 0.2
    visual: float = 0.2


class AnalysisConfig(BaseModel):
    filler_words: list[str] = Field(default_factory=list)
    keep_importance_threshold: float = 0.35

    # Multi-factor analysis: transcript is the primary signal, complemented by
    # audio delivery (loudness/pace) and on-screen visual content.
    weights: SignalWeights = Field(default_factory=SignalWeights)
    audio_enabled: bool = True
    visual_enabled: bool = True

    # Visual sampling: take at least one keyframe every N seconds plus every
    # detected scene change, capped at ``visual_max_frames`` vision-model calls.
    visual_sample_seconds: float = 25.0
    visual_max_frames: int = 40
    visual_scene_threshold: float = 0.4

    # On-screen teaching content (slides, demos, code, labs, diagrams) must never
    # be dropped just because speech is sparse: these kinds get an importance
    # floor so cleanup keeps them.
    visual_floor_importance: float = 0.6
    visual_informative_kinds: list[str] = Field(
        default_factory=lambda: [
            "presentation",
            "screen_demo",
            "code_terminal",
            "lab_hardware",
            "diagram_chart",
        ]
    )

    # Silent-but-active footage (e.g. navigating a UI, showing how to reach a
    # place) must survive cleanup: keep a window of this many seconds around
    # informative on-screen keyframes even when there is no narration.
    visual_keep_pad_seconds: float = 2.0


class ClipsConfig(BaseModel):
    max_duration_seconds: float = 30.0
    min_duration_seconds: float = 8.0
    target_count: int = 6


class LoggingConfig(BaseModel):
    level: str = "INFO"
    # Optional path to a log file or a directory (a clipmaster.log is created in
    # a directory). Set from the desktop app's Diagnostics tab; null = console only.
    file: str | None = None


class Settings(BaseModel):
    """Fully validated application configuration."""

    workspace_dir: str = "workspace"
    media: MediaConfig = Field(default_factory=MediaConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    silence: SilenceConfig = Field(default_factory=SilenceConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    clips: ClipsConfig = Field(default_factory=ClipsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def workspace_path(self) -> Path:
        """Absolute path to the working directory, created on first access."""
        path = Path(self.workspace_dir)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (returns a new dict)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping at the top level.")
    return data


def _env_overrides() -> dict[str, Any]:
    """Map a small set of environment variables onto config keys."""
    overrides: dict[str, Any] = {}
    if (val := os.getenv("CLIPMASTER_WORKSPACE")):
        overrides["workspace_dir"] = val
    if (val := os.getenv("CLIPMASTER_OLLAMA_HOST")):
        overrides.setdefault("llm", {})["host"] = val
    if (val := os.getenv("CLIPMASTER_LLM_MODEL")):
        overrides.setdefault("llm", {})["model"] = val
    if (val := os.getenv("CLIPMASTER_WHISPER_MODEL")):
        overrides.setdefault("transcription", {})["model"] = val
    if (val := os.getenv("CLIPMASTER_WHISPER_DEVICE")):
        overrides.setdefault("transcription", {})["device"] = val
    if (val := os.getenv("CLIPMASTER_LOG_LEVEL")):
        overrides.setdefault("logging", {})["level"] = val
    if (val := os.getenv("CLIPMASTER_LOG_FILE")):
        overrides.setdefault("logging", {})["file"] = val
    return overrides


def load_settings(config_path: str | os.PathLike[str] | None = None) -> Settings:
    """Load and validate settings from the layered configuration sources."""
    merged = _load_yaml(DEFAULT_CONFIG_PATH)
    merged = _deep_merge(merged, _load_yaml(LOCAL_CONFIG_PATH))
    if config_path is not None:
        merged = _deep_merge(merged, _load_yaml(Path(config_path)))
    merged = _deep_merge(merged, _env_overrides())
    return Settings.model_validate(merged)


def save_local_overrides(patch: dict[str, Any]) -> Path:
    """Merge *patch* into ``config/local.yaml`` and persist it.

    Used by the desktop app to remember choices (e.g. the active LLM model or the
    log-file path) without touching the version-controlled ``default.yaml``.
    """
    merged = _deep_merge(_load_yaml(LOCAL_CONFIG_PATH), patch)
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_CONFIG_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False, default_flow_style=False)
    return LOCAL_CONFIG_PATH
