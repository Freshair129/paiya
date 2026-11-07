"""Loopback-only HTTP entry point for the API and built mobile application."""

import os
import sys
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from paiya.api.app import app
from paiya.read.context import read_context_group_key_from_env


def _web_dist() -> Path:
    configured = os.environ.get("PAIYA_WEB_DIST")
    if configured is not None:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "web" / "dist").resolve()


def _serve_web_path(path: str) -> Response:
    if path == "v1" or path.startswith("v1/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    root = _web_dist()
    index = root / "index.html"
    if not root.is_dir() or not index.is_file():
        return PlainTextResponse("Web app is not built.", status_code=404)

    candidate = (root / path).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(index)


@app.get("/", include_in_schema=False)
def serve_web_root() -> Response:
    """Serve the built application entry point or explain that it is missing."""
    return _serve_web_path("")


@app.get("/{path:path}", include_in_schema=False)
def serve_web_path(path: str) -> Response:
    """Serve static files and fall back to the SPA entry point for client routes."""
    return _serve_web_path(path)


def _port_from_env() -> int:
    raw_port = os.environ.get("PAIYA_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as error:
        raise ValueError("PAIYA_PORT must be an integer between 1 and 65535") from error
    if not 1 <= port <= 65535:
        raise ValueError("PAIYA_PORT must be an integer between 1 and 65535")
    return port


def main() -> None:
    """Start uvicorn on loopback only; the host is intentionally not configurable."""
    try:
        read_context_group_key_from_env()
        port = _port_from_env()
    except ValueError as error:
        print(f"paiya.serve: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
