"""Dependency detection, install guidance and Ollama control for the UI.

The desktop app's Diagnostics tab renders whatever this module reports:

* which dependencies are present (ffmpeg / ffprobe / faster-whisper),
* a copy-paste ``winget`` command + download URL for anything missing,
* live Ollama status (reachable, port, version, installed models),
* a helper to start ``ollama serve`` if it is installed but not running.

Nothing here installs software silently — it only *detects* and *guides*.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

from clipmaster.config import Settings
from clipmaster.logging_setup import get_logger
from clipmaster.server.schemas import (
    DiagnosticsComponent,
    FixHint,
    OllamaModel,
    OllamaStatus,
)

logger = get_logger("server.diagnostics")

# Install guidance shown when a dependency is missing (Windows-first via winget).
_FFMPEG_FIX = FixHint(
    winget="winget install --id Gyan.FFmpeg -e",
    url="https://www.gyan.dev/ffmpeg/builds/",
    hint="Install ffmpeg (ships with ffprobe) and make sure it is on your PATH, then Refresh.",
)
_OLLAMA_FIX = FixHint(
    winget="winget install --id Ollama.Ollama -e",
    url="https://ollama.com/download",
    hint="Install Ollama, then use “Start Ollama” below and pull a model.",
)
_WHISPER_FIX = FixHint(
    winget="",
    url="https://pypi.org/project/faster-whisper/",
    hint='Install transcription extras: pip install -e ".[transcribe]"',
)


def _binary_version(binary: str) -> str | None:
    """First line of ``<binary> -version``, or ``None`` if it isn't runnable."""
    try:
        proc = subprocess.run(
            [binary, "-version"], capture_output=True, check=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = proc.stdout or proc.stderr or ""
    first = text.splitlines()[0].strip() if text.strip() else ""
    return first or "found"


def ollama_base(host: str) -> str:
    """Normalise a configured host into a ``http://host:port`` base URL."""
    base = host if "://" in host else f"http://{host}"
    return base.rstrip("/")


def ollama_port(host: str) -> int | None:
    try:
        parsed = urlparse(ollama_base(host))
        return parsed.port or (11434 if parsed.scheme == "http" else None)
    except ValueError:
        return None


def ollama_status(settings: Settings) -> OllamaStatus:
    """Probe the local Ollama server for reachability, version and models."""
    base = ollama_base(settings.llm.host)
    port = ollama_port(settings.llm.host)
    reachable = False
    version: str | None = None
    models: list[OllamaModel] = []
    error: str | None = None

    try:
        ver = httpx.get(f"{base}/api/version", timeout=5.0)
        if ver.status_code == 200:
            reachable = True
            version = ver.json().get("version")
        tags = httpx.get(f"{base}/api/tags", timeout=5.0)
        if tags.status_code == 200:
            reachable = True
            for item in tags.json().get("models", []):
                details = item.get("details") or {}
                models.append(
                    OllamaModel(
                        name=item.get("name", ""),
                        size_bytes=item.get("size"),
                        family=details.get("family"),
                        parameter_size=details.get("parameter_size"),
                    )
                )
    except httpx.HTTPError as exc:
        error = str(exc)[:140]

    models.sort(key=lambda m: m.name)
    return OllamaStatus(
        reachable=reachable,
        host=base,
        port=port,
        version=version,
        models=models,
        selected_model=settings.llm.model,
        error=error,
    )


def collect_components(settings: Settings) -> list[DiagnosticsComponent]:
    """Detect the non-Ollama dependencies and their install guidance."""
    components: list[DiagnosticsComponent] = []

    ffmpeg_v = _binary_version(settings.media.ffmpeg_bin)
    components.append(
        DiagnosticsComponent(
            name="ffmpeg",
            category="media",
            ok=ffmpeg_v is not None,
            detail=ffmpeg_v or f"'{settings.media.ffmpeg_bin}' not found on PATH",
            version=ffmpeg_v,
            fix=None if ffmpeg_v else _FFMPEG_FIX,
        )
    )

    ffprobe_v = _binary_version(settings.media.ffprobe_bin)
    components.append(
        DiagnosticsComponent(
            name="ffprobe",
            category="media",
            ok=ffprobe_v is not None,
            detail=ffprobe_v or f"'{settings.media.ffprobe_bin}' not found on PATH",
            version=ffprobe_v,
            fix=None if ffprobe_v else _FFMPEG_FIX,
        )
    )

    whisper_ok = importlib.util.find_spec("faster_whisper") is not None
    components.append(
        DiagnosticsComponent(
            name="faster-whisper",
            category="python",
            ok=whisper_ok,
            detail=(
                f"model: {settings.transcription.model} · device: {settings.transcription.device}"
                if whisper_ok
                else "Python transcription package not installed"
            ),
            version=None,
            fix=None if whisper_ok else _WHISPER_FIX,
        )
    )

    return components


def ollama_component(status: OllamaStatus) -> DiagnosticsComponent:
    """Represent Ollama in the top-level component list (mirrors OllamaStatus)."""
    if status.reachable:
        has_model = any(
            status.selected_model.split(":")[0] in m.name for m in status.models
        )
        detail = (
            f"reachable on :{status.port} · {len(status.models)} model(s)"
            if has_model
            else f"reachable, but '{status.selected_model}' is not pulled yet"
        )
        return DiagnosticsComponent(
            name="ollama",
            category="llm",
            ok=has_model,
            detail=detail,
            version=status.version,
            fix=None if has_model else _OLLAMA_FIX,
        )
    return DiagnosticsComponent(
        name="ollama",
        category="llm",
        ok=False,
        detail=status.error or "not reachable — is it installed and running?",
        version=None,
        fix=_OLLAMA_FIX,
    )


def start_ollama(settings: Settings) -> tuple[bool, str]:
    """Start ``ollama serve`` in the background if it isn't already responding."""
    if ollama_status(settings).reachable:
        return True, "Ollama is already running."

    creationflags = 0
    if sys.platform == "win32":  # pragma: no cover - platform specific
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    try:
        subprocess.Popen(  # noqa: S603,S607 - fixed command, no user input
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as exc:
        logger.warning("Could not start Ollama: %s", exc)
        return False, f"Could not start Ollama ({exc}). Is it installed?"

    # Give the server a few seconds to bind its port, then re-check.
    for _ in range(12):
        time.sleep(0.5)
        if ollama_status(settings).reachable:
            logger.info("Ollama started via Diagnostics tab")
            return True, "Ollama started."
    return False, "Launched Ollama, but it hasn't responded yet — Refresh in a moment."
