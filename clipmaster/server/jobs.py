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
from typing import Any, Literal

from clipmaster.config import Settings
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

    def start_analyze(self, path: str, *, skip_analysis: bool) -> Job:
        """Create and launch an analysis job; returns immediately."""
        source = Path(path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Input file not found: {path}")

        loop = self._loop
        if loop is None:  # pragma: no cover - defensive; set on startup
            loop = asyncio.get_event_loop()
        job = Job(id=uuid.uuid4().hex[:12], kind="analyze")
        self._jobs[job.id] = job

        def _append_and_notify(payload: dict[str, Any]) -> None:
            # Runs on the event-loop thread only.
            job.events.append(payload)
            job.updated.set()

        def _emit(payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(_append_and_notify, payload)

        bus = EventBus()
        bus.subscribe(lambda event: _emit(event.to_dict()))

        def _run() -> None:
            try:
                report = analyze_video(
                    source, self.settings, bus=bus, skip_analysis=skip_analysis
                )
                project_dir = project_dir_for(self.settings, source)
                write_markdown(report, project_dir / "analysis.md")
                job.project_id = report.project_id
                job.status = "done"
                _emit({"type": JOB_DONE, "project_id": report.project_id})
            except Exception as exc:  # noqa: BLE001 - reported to the client
                logger.exception("Analysis job %s failed", job.id)
                job.status = "error"
                job.error = str(exc)
                _emit({"type": JOB_ERROR, "message": str(exc)})

        job.status = "running"
        loop.run_in_executor(self._executor, _run)
        return job

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
