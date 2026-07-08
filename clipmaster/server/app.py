"""FastAPI application wiring the pipeline to the desktop app.

Endpoints
---------
GET  /api/health                 environment/dependency status
GET  /api/config                 safe subset of the active settings
POST /api/probe                  quick ffprobe + chunk plan for a path
POST /api/analyze                start an analysis job -> {job_id}
GET  /api/jobs/{id}              job status snapshot
WS   /ws/jobs/{id}               live ProgressEvent stream (history + updates)
GET  /api/projects               list analysed projects in the workspace
GET  /api/projects/{id}          the full analysis.json artifact
GET  /api/projects/{id}/report   the Markdown report (text/markdown)
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from clipmaster.config import Settings, load_settings
from clipmaster.logging_setup import get_logger, setup_logging
from clipmaster.models import AnalysisReport
from clipmaster.server.jobs import JOB_DONE, JOB_ERROR, JobManager
from clipmaster.server.schemas import (
    AnalyzeRequest,
    ComponentStatus,
    HealthResponse,
    JobRef,
    JobStatus,
    ProbeResponse,
    ProjectSummary,
)
from clipmaster.version import __version__

logger = get_logger("server.app")

# How long the WebSocket waits between keepalive pings when a job is idle.
_WS_IDLE_PING_SECONDS = 25.0


def _check_binary(binary: str) -> ComponentStatus:
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
        return ComponentStatus(name=binary, ok=True, detail="found on PATH")
    except (OSError, subprocess.CalledProcessError):
        return ComponentStatus(name=binary, ok=False, detail=f"'{binary}' not found")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.logging.level)

    app = FastAPI(title="ClipMaster", version=__version__)
    # Local single-user tool bound to loopback; permissive CORS is safe here and
    # lets the Vite dev server (http://localhost:5173) talk to us.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    manager = JobManager(settings)
    app.state.settings = settings
    app.state.jobs = manager

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover - lifecycle hook
        manager.shutdown()

    # --- Health & config ------------------------------------------------------
    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        components = [
            _check_binary(settings.media.ffmpeg_bin),
            _check_binary(settings.media.ffprobe_bin),
        ]
        # Ollama
        from clipmaster.analysis.ollama_client import OllamaClient

        client = OllamaClient(host=settings.llm.host, model=settings.llm.model)
        try:
            models = client.list_models()
            has = any(settings.llm.model.split(":")[0] in m for m in models)
            components.append(
                ComponentStatus(
                    name="ollama",
                    ok=has,
                    detail=(
                        f"{settings.llm.model} available"
                        if has
                        else f"pull it: ollama pull {settings.llm.model}"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            components.append(ComponentStatus(name="ollama", ok=False, detail=str(exc)[:80]))

        # faster-whisper presence
        import importlib.util

        whisper_ok = importlib.util.find_spec("faster_whisper") is not None
        components.append(
            ComponentStatus(
                name="faster-whisper",
                ok=whisper_ok,
                detail=settings.transcription.model if whisper_ok else "pip install -e .[transcribe]",
            )
        )

        return HealthResponse(
            version=__version__,
            workspace=str(settings.workspace_path),
            components=components,
        )

    @app.get("/api/config")
    def get_config() -> dict:
        return {
            "chunking": settings.chunking.model_dump(),
            "transcription": settings.transcription.model_dump(),
            "llm": {"model": settings.llm.model, "host": settings.llm.host},
            "clips": settings.clips.model_dump(),
        }

    # --- Probe ----------------------------------------------------------------
    @app.post("/api/probe", response_model=ProbeResponse)
    def probe(req: AnalyzeRequest) -> ProbeResponse:
        from clipmaster.media import plan_chunks, probe_media

        path = Path(req.path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {req.path}")
        media = probe_media(path, settings.media.ffprobe_bin)
        plan = plan_chunks(
            media.duration_s,
            max_chunk_seconds=settings.chunking.max_chunk_seconds,
            overlap_seconds=settings.chunking.overlap_seconds,
        )
        return ProbeResponse(
            duration_s=media.duration_s,
            width=media.video.width if media.video else None,
            height=media.video.height if media.video else None,
            fps=media.video.fps if media.video else None,
            audio_streams=len(media.audios),
            chunk_count=len(plan.chunks),
        )

    # --- Jobs -----------------------------------------------------------------
    @app.post("/api/analyze", response_model=JobRef)
    def analyze(req: AnalyzeRequest) -> JobRef:
        try:
            job = manager.start_analyze(req.path, skip_analysis=req.skip_analysis)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JobRef(job_id=job.id, status=job.status)

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    def job_status(job_id: str) -> JobStatus:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return JobStatus(
            job_id=job.id,
            status=job.status,
            project_id=job.project_id,
            error=job.error,
            event_count=len(job.events),
        )

    @app.websocket("/ws/jobs/{job_id}")
    async def job_stream(websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()
        job = manager.get(job_id)
        if job is None:
            await websocket.send_json({"type": JOB_ERROR, "message": "Unknown job"})
            await websocket.close(code=4004)
            return

        cursor = 0
        try:
            while True:
                # Flush any events we haven't sent yet (includes full history).
                while cursor < len(job.events):
                    event = job.events[cursor]
                    cursor += 1
                    await websocket.send_json(event)
                    if event.get("type") in (JOB_DONE, JOB_ERROR):
                        await websocket.close()
                        return

                # Wait for the next event; re-check for a race between flush+clear.
                job.updated.clear()
                if cursor < len(job.events):
                    continue
                try:
                    await asyncio.wait_for(job.updated.wait(), timeout=_WS_IDLE_PING_SECONDS)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
        except WebSocketDisconnect:
            return

    # --- Projects -------------------------------------------------------------
    @app.get("/api/projects", response_model=list[ProjectSummary])
    def list_projects() -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for analysis_path in sorted(settings.workspace_path.glob("*/analysis.json")):
            try:
                report = AnalysisReport.load(analysis_path)
            except Exception:  # noqa: BLE001 - skip corrupt/partial artifacts
                continue
            summaries.append(
                ProjectSummary(
                    project_id=report.project_id,
                    source_path=report.source_path,
                    created_at=report.created_at,
                    duration_s=report.media.duration_s,
                    chapters=len(report.chapters),
                    clips=len(report.clip_candidates),
                    has_transcript=bool(report.transcript.segments),
                )
            )
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> JSONResponse:
        path = settings.workspace_path / project_id / "analysis.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Unknown project")
        return JSONResponse(content=AnalysisReport.load(path).model_dump())

    @app.get("/api/projects/{project_id}/report")
    def get_project_report(project_id: str) -> PlainTextResponse:
        from clipmaster.report.builder import render_markdown

        path = settings.workspace_path / project_id / "analysis.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Unknown project")
        return PlainTextResponse(
            render_markdown(AnalysisReport.load(path)), media_type="text/markdown"
        )

    return app
