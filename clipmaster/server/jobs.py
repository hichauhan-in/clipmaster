"""Background job management with a thread → asyncio event bridge.

The analysis pipeline is synchronous and CPU/GPU bound, so each job runs in a
worker thread (``max_workers=1`` to avoid oversubscribing the GPU). Progress
events emitted from that thread are forwarded onto the event loop with
``loop.call_soon_threadsafe`` so the WebSocket handler can stream them.

Every event is also appended to ``job.events`` so a client that connects *after*
a job started still receives the full history before live updates.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from clipmaster.config import Settings, SignalWeights
from clipmaster.events import EventBus
from clipmaster.logging_setup import get_logger
from clipmaster.pipeline import analyze_video, project_dir_for
from clipmaster.report.builder import write_markdown

logger = get_logger("server.jobs")

JobState = Literal["pending", "running", "done", "error"]

# Sentinel event types the WebSocket handler watches for to close the stream.
JOB_DONE = "job_done"
JOB_ERROR = "job_error"


@dataclass
class Job:
    id: str
    kind: str
    status: JobState = "pending"
    project_id: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    updated: asyncio.Event = field(default_factory=asyncio.Event)


class JobManager:
    """Owns the worker pool and the registry of live/finished jobs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipmaster")
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the app's event loop (captured on startup).

        ``start_analyze`` runs inside FastAPI's sync-endpoint worker thread, which
        has no running loop of its own, so we can't call ``get_running_loop``
        there. We capture the loop once at startup and reuse it to bridge worker
        thread → event loop for progress events.
        """
        self._loop = loop

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _emitter(self, job: Job) -> Callable[[dict[str, Any]], None]:
        """Return a thread-safe ``emit(payload)`` that appends + wakes waiters.

        Analysis and the post-analysis actions all run in the worker thread but
        publish progress that the WebSocket handler (on the event loop) streams;
        this bridges the two with ``call_soon_threadsafe``.
        """
        loop = self._loop or asyncio.get_event_loop()

        def _append_and_notify(payload: dict[str, Any]) -> None:
            job.events.append(payload)
            job.updated.set()

        def _emit(payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(_append_and_notify, payload)

        return _emit

    def start_task(
        self,
        kind: str,
        work: Callable[[EventBus], dict[str, Any] | None],
    ) -> Job:
        """Run ``work(bus)`` in the worker thread as a tracked, streamable job.

        ``work`` may publish progress to the provided :class:`EventBus`; whatever
        dict it returns is merged into the terminal ``job_done`` event (e.g. the
        output directory and file list of a render action).
        """
        loop = self._loop or asyncio.get_event_loop()
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self._jobs[job.id] = job
        emit = self._emitter(job)

        bus = EventBus()
        bus.subscribe(lambda event: emit(event.to_dict()))

        def _run() -> None:
            try:
                result = work(bus) or {}
                job.status = "done"
                emit({"type": JOB_DONE, **result})
            except Exception as exc:  # noqa: BLE001 - reported to the client
                logger.exception("%s job %s failed", kind, job.id)
                job.status = "error"
                job.error = str(exc)
                emit({"type": JOB_ERROR, "message": str(exc)})

        job.status = "running"
        loop.run_in_executor(self._executor, _run)
        return job

    def start_analyze(
        self,
        path: str,
        *,
        skip_analysis: bool,
        audio_enabled: bool | None = None,
        visual_enabled: bool | None = None,
        weights: dict[str, float] | None = None,
    ) -> Job:
        """Create and launch an analysis job; returns immediately.

        ``audio_enabled`` / ``visual_enabled`` / ``weights`` are optional per-job
        overrides for the multi-factor analysis; ``None`` keeps the configured
        value. They are applied to a private copy so concurrent jobs and the
        persisted config are never mutated.
        """
        source = Path(path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Input file not found: {path}")

        settings = self.settings
        if audio_enabled is not None or visual_enabled is not None or weights is not None:
            settings = settings.model_copy(deep=True)
            if audio_enabled is not None:
                settings.analysis.audio_enabled = audio_enabled
            if visual_enabled is not None:
                settings.analysis.visual_enabled = visual_enabled
            if weights is not None:
                settings.analysis.weights = SignalWeights(**weights)

        loop = self._loop
        if loop is None:  # pragma: no cover - defensive; set on startup
            loop = asyncio.get_event_loop()
        job = Job(id=uuid.uuid4().hex[:12], kind="analyze")
        self._jobs[job.id] = job
        emit = self._emitter(job)

        bus = EventBus()
        bus.subscribe(lambda event: emit(event.to_dict()))

        def _run() -> None:
            try:
                report = analyze_video(
                    source, settings, bus=bus, skip_analysis=skip_analysis
                )
                project_dir = project_dir_for(settings, source)
                write_markdown(report, project_dir / "analysis.md")
                job.project_id = report.project_id
                job.status = "done"
                emit({"type": JOB_DONE, "project_id": report.project_id})
            except Exception as exc:  # noqa: BLE001 - reported to the client
                logger.exception("Analysis job %s failed", job.id)
                job.status = "error"
                job.error = str(exc)
                emit({"type": JOB_ERROR, "message": str(exc)})

        job.status = "running"
        loop.run_in_executor(self._executor, _run)
        return job

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
