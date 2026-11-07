"""The Paiya FastAPI application."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from paiya.api.evidence import router as fixture_read_router
from paiya.api.prices import router as price_router
from paiya.read.context import read_context_group_key_from_env
from paiya.read.errors import ReadError
from paiya.settings import get_enrich_settings, get_price_trend_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Reject an invalid server-selected read group before serving requests."""
    read_context_group_key_from_env()
    get_price_trend_settings()
    get_enrich_settings()
    yield


app = FastAPI(title="Paiya", version="0.1.0", lifespan=lifespan)
app.include_router(fixture_read_router)
app.include_router(price_router)


@app.exception_handler(ReadError)
async def read_error_response(_request: Request, error: ReadError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": {"code": error.code, "message": error.message}},
    )


@app.middleware("http")
async def disable_api_caching(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Mark every versioned API response uncacheable, including errors and media."""
    response = await call_next(request)
    if request.url.path == "/v1" or request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a database-independent process health endpoint."""
    return {"status": "ok"}
