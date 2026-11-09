"""The Paiya FastAPI application."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from paiya.api.auth import router as auth_router
from paiya.api.evidence import router as fixture_read_router
from paiya.api.health import router as health_router
from paiya.api.prices import router as price_router
from paiya.auth.request_checks import (
    READ_METHODS,
    WRITE_METHODS,
    csrf_token_matches,
    host_is_allowed,
    origin_is_allowed,
)
from paiya.auth.sessions import SESSION_COOKIE_NAME, authenticate_session
from paiya.read.context import read_context_group_key_from_env
from paiya.read.errors import ReadError
from paiya.settings import get_database_engine, get_enrich_settings, get_price_trend_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Reject an invalid server-selected read group before serving requests."""
    read_context_group_key_from_env()
    get_price_trend_settings()
    get_enrich_settings()
    yield


app = FastAPI(title="Paiya", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(fixture_read_router)
app.include_router(price_router)
app.include_router(health_router)


def _request_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = {"Cache-Control": "no-store"}
    if headers is not None:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers=response_headers,
    )


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
    """Apply request checks and mark every versioned API response uncacheable."""
    if not host_is_allowed(request):
        return _request_error(403, "origin_not_allowed", "Host is not allowed")
    if request.method not in READ_METHODS | WRITE_METHODS:
        return _request_error(
            405,
            "method_not_allowed",
            "Method is not allowed",
            headers={"Allow": ", ".join(sorted(READ_METHODS | WRITE_METHODS))},
        )
    if request.method in WRITE_METHODS and not origin_is_allowed(request):
        return _request_error(403, "origin_not_allowed", "Origin is not allowed")
    if request.method in WRITE_METHODS and request.url.path != "/v1/auth/unlock":
        token = request.cookies.get(SESSION_COOKIE_NAME)
        with get_database_engine().begin() as connection:
            session = authenticate_session(connection, token)
        if session is None:
            return _request_error(401, "session_required", "A valid owner session is required")
        csrf_token = request.headers.get("x-csrf-token")
        if not csrf_token_matches(csrf_token, session.csrf_token):
            return _request_error(403, "csrf_invalid", "A valid CSRF token is required")
        request.state.owner_session = session
    elif request.method in READ_METHODS and request.url.path.startswith("/v1/"):
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token is not None:
            with get_database_engine().begin() as connection:
                session = authenticate_session(connection, token)
            if session is not None:
                request.state.owner_session = session
    response = await call_next(request)
    if request.url.path == "/v1" or request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a database-independent process health endpoint."""
    return {"status": "ok"}
