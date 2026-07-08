"""A minimal HTTP client for a local Ollama server.

We talk to Ollama's REST API directly with ``httpx`` rather than pulling in the
official SDK: fewer dependencies, and full control over JSON-mode requests and
timeouts. Only the two endpoints we need are wrapped:

* ``/api/chat``     -> :meth:`OllamaClient.chat`
* ``/api/tags``     -> :meth:`OllamaClient.list_models` (health / preflight check)
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from clipmaster.logging_setup import get_logger

logger = get_logger("analysis.ollama")

# Matches the first balanced-looking JSON object or array in a string.
_JSON_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


class OllamaError(RuntimeError):
    """Raised when the Ollama server is unreachable or returns an error."""


class OllamaClient:
    """Thin synchronous wrapper around a local Ollama instance."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        *,
        temperature: float = 0.2,
        timeout: float = 300.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    # --- Health ---------------------------------------------------------------
    def list_models(self) -> list[str]:
        """Return the model names available on the server (raises on failure)."""
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=10.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}. Is `ollama serve` running? ({exc})"
            ) from exc
        return [m.get("name", "") for m in resp.json().get("models", [])]

    def is_available(self) -> bool:
        """Best-effort check that the server responds; never raises."""
        try:
            self.list_models()
            return True
        except OllamaError:
            return False

    # --- Chat -----------------------------------------------------------------
    def chat(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        model: str | None = None,
    ) -> str:
        """Send a single-turn chat request and return the assistant text."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if json_mode:
            payload["format"] = "json"

        try:
            resp = httpx.post(
                f"{self.host}/api/chat", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama chat request failed: {exc}") from exc

        data = resp.json()
        return (data.get("message", {}) or {}).get("content", "") or ""

    def chat_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        """Chat in JSON mode and parse the response into Python objects."""
        raw = self.chat(prompt, system=system, json_mode=True, model=model)
        return self._parse_json(raw)

    # --- Vision ---------------------------------------------------------------
    def vision_json(
        self,
        prompt: str,
        image_b64: str,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        """Send a single image + prompt to a multimodal model, parse JSON reply.

        Uses Ollama's ``/api/chat`` ``images`` field (base64, no data: prefix),
        supported by vision models such as ``qwen2.5vl`` and ``llava``.
        """
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt, "images": [image_b64]})

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        try:
            resp = httpx.post(
                f"{self.host}/api/chat", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama vision request failed: {exc}") from exc

        data = resp.json()
        raw = (data.get("message", {}) or {}).get("content", "") or ""
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> Any:
        """Best-effort extraction of a JSON value from a model response."""
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        match = _JSON_BLOCK_RE.search(raw)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        raise OllamaError(f"Model did not return valid JSON. Got: {raw[:200]!r}")
