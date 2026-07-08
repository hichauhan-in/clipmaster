"""Abstract transcription provider and a small factory.

Adding a new backend (e.g. ``whisper.cpp`` with Vulkan for AMD GPUs on Windows,
or a cloud API) means implementing :class:`Transcriber` and registering it in
:func:`get_transcriber` — nothing else in the codebase changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from clipmaster.config import TranscriptionConfig
from clipmaster.models import TranscriptSegment


@dataclass
class TranscriptionResult:
    """Segments (local timeline) plus the detected language."""

    language: str | None
    segments: list[TranscriptSegment] = field(default_factory=list)


class Transcriber(ABC):
    """Interface every speech-to-text backend implements."""

    def __init__(self, config: TranscriptionConfig) -> None:
        self.config = config

    @abstractmethod
    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        """Transcribe ``audio_path`` and return segments starting at t=0."""
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - optional cleanup hook
        """Release any model/GPU resources. Safe to call multiple times."""


def get_transcriber(config: TranscriptionConfig) -> Transcriber:
    """Instantiate the transcriber named by ``config.provider``."""
    provider = config.provider.lower()
    if provider in {"faster_whisper", "faster-whisper", "whisper"}:
        from clipmaster.transcription.faster_whisper_provider import FasterWhisperTranscriber

        return FasterWhisperTranscriber(config)
    raise ValueError(
        f"Unknown transcription provider '{config.provider}'. "
        "Available: faster_whisper."
    )
