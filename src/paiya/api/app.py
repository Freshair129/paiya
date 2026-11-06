"""The Paiya FastAPI application."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from paiya.api.evidence import router as fixture_read_router
from paiya.read.errors import ReadError

app = FastAPI(title="Paiya", version="0.1.0")
app.include_router(fixture_read_router)


@app.exception_handler(ReadError)
async def read_error_response(_request: Request, error: ReadError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": {"code": error.code, "message": error.message}},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a database-independent process health endpoint."""
    return {"status": "ok"}
