"""Run the ClipMaster API server: ``python -m clipmaster.server``.

Reads ``CLIPMASTER_SERVER_HOST`` / ``CLIPMASTER_SERVER_PORT`` (defaults
127.0.0.1:8756). The desktop app spawns the server this way as a sidecar.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from clipmaster.server.app import create_app

    host = os.getenv("CLIPMASTER_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("CLIPMASTER_SERVER_PORT", "8756"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
