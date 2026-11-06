"""The Paiya FastAPI application."""

from fastapi import FastAPI

from paiya.api.evidence import router as fixture_read_router

app = FastAPI(title="Paiya", version="0.1.0")
app.include_router(fixture_read_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a database-independent process health endpoint."""
    return {"status": "ok"}
