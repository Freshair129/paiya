"""The Paiya FastAPI application."""

from fastapi import FastAPI

app = FastAPI(title="Paiya", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a database-independent process health endpoint."""
    return {"status": "ok"}
