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
DELETE /api/projects/{id}        remove a project's folder from the workspace
POST /api/projects/{id}/notes    generate Markdown study notes -> {job_id}
POST /api/projects/{id}/cleanup  render the cleaned-up cut -> {job_id}
POST /api/projects/{id}/shorts   render vertical short clips -> {job_id}
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from clipmaster.config import Settings, load_settings
from clipmaster.logging_setup import get_logger, setup_logging
from clipmaster.models import AnalysisReport
from clipmaster.server.jobs import JOB_DONE, JOB_ERROR, JobManager
from clipmaster.server.ollama_pull import PullManager, PullState
from clipmaster.server.schemas import (
    ActionResult,
    AnalyzeRequest,
    CleanupRequest,
    ComponentStatus,
    DiagnosticsResponse,
    HealthResponse,
    JobRef,
    JobStatus,
    LogInfo,
    LogPathRequest,
    LogsResponse,
    NotesRequest,
    ProbeResponse,
    ProjectSummary,
    PullRequest,
    PullStatus,
    PythonInfo,
    SelectModelRequest,
    ShortsRequest,
)
from clipmaster.version import __version__

logger = get_logger("server.app")

# How long the WebSocket waits between keepalive pings when a job is idle.
_WS_IDLE_PING_SECONDS = 25.0


def _pull_status(state: PullState) -> PullStatus:
    return PullStatus(
        pull_id=state.pull_id,
        model=state.model,
        status=state.status,
        percent=state.percent,
        message=state.message,
        done=state.done,
        error=state.error,
    )


def _check_binary(binary: str) -> ComponentStatus:
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
        return ComponentStatus(name=binary, ok=True, detail="found on PATH")
    except (OSError, subprocess.CalledProcessError):
        return ComponentStatus(name=binary, ok=False, detail=f"'{binary}' not found")


def _slugify_stem(source_path: str) -> str:
    """A filesystem-safe slug of a source video's file stem (for output folders)."""
    import re

    stem = Path(source_path).stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:48].strip("-")
    return slug or "video"


def _action_done(
    kind: str, output_dir: Path, files: list[Path], message: str
) -> dict[str, Any]:
    """Terminal ``job_done`` payload for a render/notes action."""
    return {
        "kind": kind,
        "output_dir": str(output_dir),
        "files": [str(f) for f in files],
        "message": message,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.logging.level, settings.logging.file)

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
    pulls = PullManager(settings.llm.host)
    app.state.settings = settings
    app.state.jobs = manager
    app.state.pulls = pulls

    @app.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - lifecycle hook
        # Capture the running event loop so background analysis jobs (started from
        # FastAPI's sync-endpoint worker thread) can bridge progress events back.
        manager.set_loop(asyncio.get_running_loop())

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

    # --- Diagnostics ----------------------------------------------------------
    @app.get("/api/diagnostics", response_model=DiagnosticsResponse)
    def diagnostics() -> DiagnosticsResponse:
        from clipmaster.logging_setup import current_log_file
        from clipmaster.server.diagnostics import (
            collect_components,
            ollama_component,
            ollama_status,
        )

        oll = ollama_status(settings)
        components = collect_components(settings)
        components.append(ollama_component(oll))
        log_path = current_log_file()
        return DiagnosticsResponse(
            version=__version__,
            workspace=str(settings.workspace_path),
            python=PythonInfo(version=sys.version.split()[0], executable=sys.executable),
            components=components,
            ollama=oll,
            log=LogInfo(
                path=str(log_path) if log_path else None,
                level=settings.logging.level,
            ),
        )

    @app.post("/api/ollama/start", response_model=ActionResult)
    def ollama_start() -> ActionResult:
        from clipmaster.server.diagnostics import start_ollama

        ok, message = start_ollama(settings)
        return ActionResult(ok=ok, message=message)

    @app.post("/api/settings/model", response_model=ActionResult)
    def select_model(req: SelectModelRequest) -> ActionResult:
        from clipmaster.config import save_local_overrides

        model = req.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model name is required")
        settings.llm.model = model
        save_local_overrides({"llm": {"model": model}})
        logger.info("Active LLM model set to %s", model)
        return ActionResult(ok=True, message=f"Now using {model} for analysis.")

    @app.post("/api/settings/vision-model", response_model=ActionResult)
    def select_vision_model(req: SelectModelRequest) -> ActionResult:
        from clipmaster.config import save_local_overrides

        model = req.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model name is required")
        settings.llm.vision_model = model
        save_local_overrides({"llm": {"vision_model": model}})
        logger.info("Active vision model set to %s", model)
        return ActionResult(ok=True, message=f"Now using {model} for on-screen analysis.")

    @app.post("/api/ollama/pull", response_model=PullStatus)
    def ollama_pull(req: PullRequest) -> PullStatus:
        model = req.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model name is required")
        return _pull_status(pulls.start(model))

    @app.get("/api/ollama/pull/{pull_id}", response_model=PullStatus)
    def ollama_pull_status(pull_id: str) -> PullStatus:
        state = pulls.get(pull_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Unknown pull")
        return _pull_status(state)

    # --- Logs -----------------------------------------------------------------
    @app.get("/api/logs", response_model=LogsResponse)
    def get_logs(limit: int = 200) -> LogsResponse:
        from clipmaster.logging_setup import current_log_file, recent_log_lines

        log_path = current_log_file()
        return LogsResponse(
            path=str(log_path) if log_path else None,
            level=settings.logging.level,
            lines=recent_log_lines(max(1, min(limit, 1000))),
        )

    @app.post("/api/logs/path", response_model=ActionResult)
    def set_log_path(req: LogPathRequest) -> ActionResult:
        from clipmaster.config import save_local_overrides
        from clipmaster.logging_setup import configure_file_logging

        path = req.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="A folder or file path is required")
        try:
            resolved = configure_file_logging(path)
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"Cannot write logs to that location: {exc}"
            ) from exc
        settings.logging.file = str(resolved)
        save_local_overrides({"logging": {"file": str(resolved)}})
        return ActionResult(ok=True, message=f"Logging to {resolved}")

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
            job = manager.start_analyze(
                req.path,
                skip_analysis=req.skip_analysis,
                audio_enabled=req.audio_enabled,
                visual_enabled=req.visual_enabled,
                weights=req.weights.model_dump() if req.weights else None,
            )
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
    def _project_dir(project_id: str) -> Path:
        """Resolve a project id to its workspace folder, guarding against traversal.

        ``project_id`` arrives as a URL path segment, so we resolve it and require
        the result to be a *direct child* of the workspace — a crafted id such as
        ".." or an absolute path can never escape the workspace.
        """
        workspace = settings.workspace_path.resolve()
        project_dir = (settings.workspace_path / project_id).resolve()
        if project_dir.parent != workspace or not project_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown project")
        return project_dir

    def _load_report(project_id: str) -> tuple[AnalysisReport, Path]:
        project_dir = _project_dir(project_id)
        path = project_dir / "analysis.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Unknown project")
        return AnalysisReport.load(path), project_dir

    def _resolve_output_dir(req_dir: str | None, default: Path) -> Path:
        if not req_dir or not req_dir.strip():
            return default
        try:
            target = Path(req_dir).expanduser()
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"Cannot write to that folder: {exc}"
            ) from exc
        return target

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
        report, _ = _load_report(project_id)
        return JSONResponse(content=report.model_dump())

    @app.get("/api/projects/{project_id}/report")
    def get_project_report(project_id: str) -> PlainTextResponse:
        from clipmaster.report.builder import render_markdown

        report, _ = _load_report(project_id)
        return PlainTextResponse(render_markdown(report), media_type="text/markdown")

    @app.delete("/api/projects/{project_id}", response_model=ActionResult)
    def delete_project(project_id: str) -> ActionResult:
        project_dir = _project_dir(project_id)
        shutil.rmtree(project_dir)
        logger.info("Deleted project %s", project_id)
        return ActionResult(ok=True, message="Project deleted.")

    # --- Post-analysis actions (notes / cleanup / shorts) --------------------
    @app.post("/api/projects/{project_id}/notes", response_model=JobRef)
    def make_notes(project_id: str, req: NotesRequest) -> JobRef:
        from clipmaster.actions import build_notes, build_transcript

        if not req.notes and not req.transcript:
            raise HTTPException(
                status_code=400,
                detail="Choose at least one output: study notes or the transcript.",
            )

        report, project_dir = _load_report(project_id)
        stem = _slugify_stem(report.source_path)
        default = project_dir / "notes"
        parent = _resolve_output_dir(req.output_dir, default.parent if req.output_dir else default)
        out_dir = (parent / f"{stem}-notes") if req.output_dir else default

        def _work(bus: Any) -> dict[str, Any]:
            files: list[Path] = []
            messages: list[str] = []
            if req.notes:
                result = build_notes(report, settings, output_dir=out_dir, bus=bus)
                files.extend(result.files)
                messages.append(result.message)
            if req.transcript:
                tr = build_transcript(
                    report,
                    settings,
                    output_dir=out_dir,
                    bus=bus,
                    include_timestamps=req.transcript_timestamps,
                )
                files.extend(tr.files)
                messages.append(tr.message)
            return _action_done("notes", out_dir, files, " ".join(messages))

        job = manager.start_task("notes", _work)
        return JobRef(job_id=job.id, status=job.status)

    @app.post("/api/projects/{project_id}/cleanup", response_model=JobRef)
    def make_cleanup(project_id: str, req: CleanupRequest) -> JobRef:
        from clipmaster.actions import build_cleanup

        report, project_dir = _load_report(project_id)
        out_dir = _resolve_output_dir(req.output_dir, project_dir / "cleanup")

        def _work(bus: Any) -> dict[str, Any]:
            result = build_cleanup(report, settings, output_dir=out_dir, bus=bus)
            return _action_done("cleanup", result.output_dir, result.files, result.message)

        job = manager.start_task("cleanup", _work)
        return JobRef(job_id=job.id, status=job.status)

    @app.post("/api/projects/{project_id}/shorts", response_model=JobRef)
    def make_shorts(project_id: str, req: ShortsRequest) -> JobRef:
        from clipmaster.actions import build_shorts

        report, project_dir = _load_report(project_id)
        out_dir = _resolve_output_dir(req.output_dir, project_dir / "shorts")

        def _work(bus: Any) -> dict[str, Any]:
            result = build_shorts(
                report,
                settings,
                min_seconds=req.min_seconds,
                max_seconds=req.max_seconds,
                count=req.count,
                output_dir=out_dir,
                style=req.style,
                card_backgrounds=req.backgrounds,
                bus=bus,
            )
            return _action_done("shorts", result.output_dir, result.files, result.message)

        job = manager.start_task("shorts", _work)
        return JobRef(job_id=job.id, status=job.status)

    return app
