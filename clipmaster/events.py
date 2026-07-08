"""A tiny progress/event bus shared by every front-end.

The pipeline emits :class:`ProgressEvent` objects describing *what is happening
right now*. Consumers subscribe with a callback:

* the **CLI** renders them as a live Rich progress display,
* the **HTTP server** (later milestone) rebroadcasts them over a WebSocket so the
  desktop editor can show the same live status the user asked for.

Keeping this decoupled means the core pipeline never imports UI code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Stage(str, Enum):
    """High-level phases of the pipeline, surfaced verbatim in the UI."""

    INGEST = "ingest"
    PROBE = "probe"
    CHUNK = "chunk"
    EXTRACT_AUDIO = "extract_audio"
    SILENCE = "silence"
    TRANSCRIBE = "transcribe"
    AUDIO_ANALYSIS = "audio_analysis"
    VISUAL_ANALYSIS = "visual_analysis"
    ANALYZE = "analyze"
    REPORT = "report"
    CLEANUP = "cleanup"
    CLIPS = "clips"
    EDIT = "edit"
    DONE = "done"


class EventType(str, Enum):
    STAGE_START = "stage_start"
    PROGRESS = "progress"
    LOG = "log"
    STAGE_END = "stage_end"
    ERROR = "error"


@dataclass
class ProgressEvent:
    """A single point-in-time status update."""

    type: EventType
    stage: Stage
    message: str = ""
    # 0.0..1.0 completion of the current stage, when known.
    fraction: float | None = None
    # Free-form structured payload (durations, counts, paths, ...).
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "stage": self.stage.value,
            "message": self.message,
            "fraction": self.fraction,
            "data": self.data,
            "timestamp": self.timestamp,
        }


EventCallback = Callable[[ProgressEvent], None]


class EventBus:
    """Fan-out of :class:`ProgressEvent` to any number of subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[EventCallback] = []

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Register ``callback``; returns a function that unsubscribes it."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def emit(self, event: ProgressEvent) -> None:
        for callback in list(self._subscribers):
            # A misbehaving subscriber must never break the pipeline.
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - defensive fan-out
                pass

    # --- Convenience emitters -------------------------------------------------
    def stage_start(self, stage: Stage, message: str = "", **data: Any) -> None:
        self.emit(ProgressEvent(EventType.STAGE_START, stage, message, data=data))

    def progress(
        self, stage: Stage, fraction: float, message: str = "", **data: Any
    ) -> None:
        self.emit(
            ProgressEvent(EventType.PROGRESS, stage, message, fraction=fraction, data=data)
        )

    def log(self, stage: Stage, message: str, **data: Any) -> None:
        self.emit(ProgressEvent(EventType.LOG, stage, message, data=data))

    def stage_end(self, stage: Stage, message: str = "", **data: Any) -> None:
        self.emit(ProgressEvent(EventType.STAGE_END, stage, message, data=data))

    def error(self, stage: Stage, message: str, **data: Any) -> None:
        self.emit(ProgressEvent(EventType.ERROR, stage, message, data=data))


# A process-wide default bus for callers that don't manage their own.
default_bus = EventBus()
