"""ClipMaster command-line interface.

The CLI is a thin shell over the same core library the desktop app uses, so every
feature is testable from the terminal during development:

    clipmaster doctor                         # check ffmpeg / ollama / models
    clipmaster info  path/to/video.mp4        # ffprobe summary
    clipmaster analyze path/to/video.mp4      # full analysis -> analysis.json + .md
    clipmaster report <project_id|json>       # re-render the Markdown report

Run ``clipmaster --help`` for the full list.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from clipmaster.config import load_settings
from clipmaster.events import EventBus, EventType, ProgressEvent, Stage
from clipmaster.logging_setup import setup_logging
from clipmaster.version import __version__

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="ClipMaster — local-first video analysis & editing pipeline.",
)
console = Console()


# --- Shared helpers ----------------------------------------------------------
def _load(config: str | None):
    settings = load_settings(config)
    setup_logging(settings.logging.level)
    return settings


def _console_subscriber(event: ProgressEvent) -> None:
    """Render pipeline events as concise, colourful console lines."""
    stage = event.stage.value
    if event.type is EventType.STAGE_START:
        console.print(f"[bold cyan]▶ {stage}[/] {event.message}")
    elif event.type is EventType.STAGE_END:
        console.print(f"[green]✓ {stage}[/] {event.message}")
    elif event.type is EventType.PROGRESS and event.fraction is not None:
        console.print(f"  [dim]{event.fraction * 100:5.1f}%[/] {event.message}")
    elif event.type is EventType.ERROR:
        console.print(f"[bold red]✗ {stage}[/] {event.message}")
    elif event.type is EventType.LOG:
        console.print(f"  [dim]{event.message}[/]")


def _fmt(seconds: float) -> str:
    from clipmaster.report.builder import format_timestamp

    return format_timestamp(seconds)


# --- Commands ----------------------------------------------------------------
@app.command()
def version() -> None:
    """Print the ClipMaster version."""
    console.print(f"ClipMaster {__version__}")


@app.command()
def doctor(config: str = typer.Option(None, "--config", "-c", help="Config file path")) -> None:
    """Check that ffmpeg, ffprobe and Ollama are reachable."""
    settings = _load(config)
    table = Table(title="ClipMaster environment check", show_header=True)
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Detail")

    # ffmpeg / ffprobe
    from clipmaster.media.ffmpeg import FFmpegError, run_ffprobe

    for name, binary in (
        ("ffmpeg", settings.media.ffmpeg_bin),
        ("ffprobe", settings.media.ffprobe_bin),
    ):
        try:
            import subprocess

            subprocess.run([binary, "-version"], capture_output=True, check=True)
            table.add_row(name, "[green]ok[/]", binary)
        except (OSError, subprocess.CalledProcessError):
            table.add_row(name, "[red]missing[/]", f"'{binary}' not found on PATH")

    # Ollama
    from clipmaster.analysis.ollama_client import OllamaClient

    client = OllamaClient(host=settings.llm.host, model=settings.llm.model)
    try:
        models = client.list_models()
        has_model = any(settings.llm.model.split(":")[0] in m for m in models)
        status = "[green]ok[/]" if has_model else "[yellow]model missing[/]"
        detail = (
            f"{settings.llm.model} available"
            if has_model
            else f"pull it: `ollama pull {settings.llm.model}`"
        )
        table.add_row("ollama", status, detail)
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        table.add_row("ollama", "[red]offline[/]", str(exc)[:60])

    # faster-whisper
    try:
        import importlib.util

        spec = importlib.util.find_spec("faster_whisper")
        if spec is not None:
            table.add_row("faster-whisper", "[green]ok[/]", settings.transcription.model)
        else:
            table.add_row(
                "faster-whisper", "[yellow]not installed[/]", "pip install -e .[transcribe]"
            )
    except Exception:  # noqa: BLE001
        table.add_row("faster-whisper", "[yellow]unknown[/]", "")

    console.print(table)


@app.command()
def info(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input video"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Probe a video and print its media info + chunk plan."""
    settings = _load(config)
    from clipmaster.media import plan_chunks, probe_media

    media = probe_media(video, settings.media.ffprobe_bin)
    plan = plan_chunks(
        media.duration_s,
        max_chunk_seconds=settings.chunking.max_chunk_seconds,
        overlap_seconds=settings.chunking.overlap_seconds,
    )

    res = f"{media.video.width}x{media.video.height}" if media.video else "unknown"
    body = (
        f"[bold]{video.name}[/]\n"
        f"Duration : {_fmt(media.duration_s)} ({media.duration_s:.1f}s)\n"
        f"Video    : {res} @ {media.video.fps if media.video else '?'} fps\n"
        f"Audio    : {len(media.audios)} stream(s)\n"
        f"Chunks   : {len(plan.chunks)} × ≤{settings.chunking.max_chunk_seconds / 60:.0f} min"
    )
    console.print(Panel(body, title="Media info", border_style="cyan"))
    for c in plan.chunks:
        console.print(f"  chunk {c.index}: {_fmt(c.start_s)} → {_fmt(c.end_s)} ({c.duration_s:.0f}s)")


@app.command()
def analyze(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input video"),
    config: str = typer.Option(None, "--config", "-c"),
    skip_analysis: bool = typer.Option(
        False, "--skip-analysis", help="Transcript + silence only; skip the LLM step"
    ),
) -> None:
    """Run the full analysis pipeline on a video."""
    settings = _load(config)
    from clipmaster.pipeline import analyze_video
    from clipmaster.report.builder import write_markdown

    bus = EventBus()
    bus.subscribe(_console_subscriber)

    try:
        report = analyze_video(video, settings, bus=bus, skip_analysis=skip_analysis)
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        console.print(f"[bold red]Analysis failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    from clipmaster.pipeline import project_dir_for

    project_dir = project_dir_for(settings, video)
    md_path = write_markdown(report, project_dir / "analysis.md")

    console.print()
    console.print(
        Panel(
            f"Project   : [bold]{report.project_id}[/]\n"
            f"Segments  : {len(report.transcript.segments)}\n"
            f"Chapters  : {len(report.chapters)}\n"
            f"Clips     : {len(report.clip_candidates)}\n"
            f"Silences  : {len(report.silences)}\n"
            f"Report    : {md_path}\n"
            f"JSON      : {project_dir / 'analysis.json'}",
            title="✓ Analysis complete",
            border_style="green",
        )
    )


@app.command()
def report(
    target: str = typer.Argument(..., help="Project id or path to analysis.json"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Re-render the Markdown report from an existing analysis.json."""
    settings = _load(config)
    from clipmaster.models import AnalysisReport
    from clipmaster.report.builder import render_markdown

    candidate = Path(target)
    if not candidate.exists():
        candidate = settings.workspace_path / target / "analysis.json"
    if not candidate.exists():
        console.print(f"[red]Could not find analysis.json for '{target}'.[/]")
        raise typer.Exit(code=1)

    report_obj = AnalysisReport.load(candidate)
    console.print(render_markdown(report_obj))


@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (loopback only by default)"),
    port: int = typer.Option(8756, "--port", help="Port for the HTTP/WebSocket API"),
) -> None:
    """Run the HTTP + WebSocket API server the desktop app connects to."""
    settings = _load(config)
    try:
        import uvicorn

        from clipmaster.server.app import create_app
    except ImportError as exc:  # pragma: no cover - env dependent
        console.print(
            "[red]Server extras not installed.[/] Run: pip install -e \".[server]\""
        )
        raise typer.Exit(code=1) from exc

    console.print(
        Panel(
            f"ClipMaster API on [bold]http://{host}:{port}[/]\n"
            f"Workspace: {settings.workspace_path}",
            title="serve",
            border_style="cyan",
        )
    )
    uvicorn.run(create_app(settings), host=host, port=port, log_level=settings.logging.level.lower())


if __name__ == "__main__":  # pragma: no cover
    app()
