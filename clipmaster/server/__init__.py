"""HTTP server that exposes the ClipMaster pipeline to the desktop app.

* REST for actions and reads.
* A WebSocket per job that streams the pipeline's :class:`ProgressEvent`s so the
  editor can render live status — the same events the CLI prints.

Bind to 127.0.0.1 only: this is a single-user local tool, never a public service.
"""

from clipmaster.server.app import create_app

__all__ = ["create_app"]
