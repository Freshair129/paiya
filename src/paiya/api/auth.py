"""Owner authentication routes for the loopback HTTP API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from paiya.auth.credentials import (
    AuthConfigurationError,
    AuthenticationThrottled,
    AuthNotConfigured,
    verify_owner_passphrase,
)
from paiya.auth.nonces import consume_challenge, issue_challenge
from paiya.auth.request_checks import csrf_token_matches
from paiya.auth.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    AuthenticatedSession,
    authenticate_session,
    create_session,
    revoke_all_sessions,
    revoke_session,
    revoke_session_by_token,
)
from paiya.settings import get_database_engine

router = APIRouter(prefix="/v1/auth")


@router.get("/challenge")
def get_challenge() -> dict[str, str]:
    """Issue the one-use nonce required before passphrase unlock."""
    return {"nonce": issue_challenge()}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _session_required() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "session_required", "message": "A valid owner session is required"},
    )


def require_owner_session(request: Request) -> AuthenticatedSession:
    """Validate the host-only session cookie and refresh its idle timestamp."""
    cached = getattr(request.state, "owner_session", None)
    if isinstance(cached, AuthenticatedSession):
        return cached
    token = request.cookies.get(SESSION_COOKIE_NAME)
    with get_database_engine().begin() as connection:
        session = authenticate_session(connection, token)
    if session is None:
        raise _session_required()
    return session


OwnerSession = Annotated[AuthenticatedSession, Depends(require_owner_session)]


def _require_csrf(session: AuthenticatedSession, token: str | None) -> None:
    if not csrf_token_matches(token, session.csrf_token):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_invalid", "message": "A valid CSRF token is required"},
        )


@router.get("/csrf")
def get_csrf(session: OwnerSession) -> dict[str, str]:
    """Return the synchronizer token for a page reload after validating the session."""
    return {"csrf_token": session.csrf_token}


@router.post("/unlock")
async def unlock(
    request: Request, x_csrf_token: Annotated[str | None, Header()] = None
) -> Response:
    """Exchange the one-use pre-auth nonce and passphrase for a new session."""
    if x_csrf_token is None or not consume_challenge(x_csrf_token):
        return _error(403, "csrf_invalid", "A valid pre-authentication CSRF token is required")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return _error(422, "invalid_request", "A passphrase is required")
    passphrase = body.get("passphrase") if isinstance(body, dict) else None
    if not isinstance(passphrase, str):
        return _error(422, "invalid_request", "A passphrase is required")
    try:
        if not verify_owner_passphrase(passphrase):
            return _error(401, "session_required", "The passphrase was not accepted")
    except AuthenticationThrottled:
        return _error(429, "unlock_throttled", "Unlock attempts are temporarily limited")
    except AuthNotConfigured:
        return _error(503, "owner_auth_not_configured", "Owner authentication is not configured")
    except AuthConfigurationError:
        return _error(503, "owner_auth_unavailable", "Owner authentication is unavailable")

    with get_database_engine().begin() as connection:
        revoke_session_by_token(connection, request.cookies.get(SESSION_COOKIE_NAME))
        session = create_session(connection)
    response = JSONResponse(
        {"session_id": str(session.session_id), "csrf_token": session.csrf_token},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.token,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        domain=None,
        secure=False,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
def logout(
    session: OwnerSession,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Response:
    """Revoke the current session after synchronizer-CSRF validation."""
    _require_csrf(session, x_csrf_token)
    with get_database_engine().begin() as connection:
        revoke_session(connection, session.session_id)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        domain=None,
        secure=False,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/revoke-all")
def revoke_all(
    session: OwnerSession,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Response:
    """Revoke every session after synchronizer-CSRF validation."""
    _require_csrf(session, x_csrf_token)
    with get_database_engine().begin() as connection:
        revoke_all_sessions(connection)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        domain=None,
        secure=False,
        httponly=True,
        samesite="strict",
    )
    return response
