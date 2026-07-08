"""Background manager for ``ollama pull`` with progress tracking.

Pulling a model is a long streaming operation. We run each pull on a daemon
thread that consumes Ollama's NDJSON progress stream and updates an in-memory
:class:`PullState`; the desktop app polls ``/api/ollama/pull/{id}`` for progress.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass

import httpx

from clipmaster.logging_setup import get_logger

logger = get_logger("server.pull")


@dataclass
class PullState:
    pull_id: str
    model: str
    status: str = "starting"
    percent: float = 0.0
    message: str = "Starting download…"
    done: bool = False
    error: str | None = None


class PullManager:
    """Tracks concurrent ``ollama pull`` operations by id."""

    def __init__(self, host: str) -> None:
        base = host if "://" in host else f"http://{host}"
        self._base = base.rstrip("/")
        self._states: dict[str, PullState] = {}
        self._lock = threading.Lock()

    def start(self, model: str) -> PullState:
        pull_id = uuid.uuid4().hex[:12]
        state = PullState(pull_id=pull_id, model=model)
        with self._lock:
            self._states[pull_id] = state
        threading.Thread(target=self._run, args=(state,), daemon=True).start()
        logger.info("Started pull of %s (%s)", model, pull_id)
        return state

    def get(self, pull_id: str) -> PullState | None:
        with self._lock:
            return self._states.get(pull_id)

    # --- worker ---------------------------------------------------------------
    def _run(self, state: PullState) -> None:
        url = f"{self._base}/api/pull"
        try:
            with httpx.stream(
                "POST", url, json={"model": state.model, "stream": True}, timeout=None
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        self._apply(state, line)
            if state.error is None:
                state.status = "success"
                state.percent = 100.0
                state.message = f"{state.model} is ready."
                logger.info("Pull complete: %s", state.model)
        except httpx.HTTPError as exc:
            state.error = f"Pull failed: {exc}"
            state.message = state.error
            logger.warning("Pull of %s failed: %s", state.model, exc)
        finally:
            state.done = True

    @staticmethod
    def _apply(state: PullState, line: str) -> None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if data.get("error"):
            state.error = str(data["error"])
            state.message = state.error
            return
        status = data.get("status", "")
        if status:
            state.status = status
        total = data.get("total")
        completed = data.get("completed")
        if total:
            state.percent = round((completed or 0) / total * 100, 1)
            state.message = f"{status} — {state.percent:.0f}%"
        elif status:
            state.message = status
