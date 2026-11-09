"""PostgreSQL-backed owner sessions for the loopback API."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, insert, select, update

from paiya.store.tables import owner_sessions

SESSION_COOKIE_NAME = "paiya_owner_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
SESSION_IDLE_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """New response credentials; only the token digest is persisted."""

    session_id: UUID
    token: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Validated server-side session state safe for request dependencies."""

    session_id: UUID
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


def session_token_hash(token: str) -> bytes:
    """Return the database representation of a session token."""
    return hashlib.sha256(token.encode("ascii")).digest()


def create_session(conn: Connection, *, now: datetime | None = None) -> CreatedSession:
    """Persist a new session while returning its raw cookie token once."""
    created_at = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = created_at + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    session_id = uuid4()
    conn.execute(
        insert(owner_sessions).values(
            id=session_id,
            token_hash=session_token_hash(token),
            csrf_token=csrf_token,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
        )
    )
    return CreatedSession(session_id, token, csrf_token, created_at, expires_at)


def _find_session(conn: Connection, token: str) -> dict[str, object] | None:
    try:
        digest = session_token_hash(token)
    except UnicodeEncodeError:
        return None
    row = (
        conn.execute(select(owner_sessions).where(owner_sessions.c.token_hash == digest))
        .mappings()
        .one_or_none()
    )
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    return dict(row)


def authenticate_session(
    conn: Connection, token: str | None, *, now: datetime | None = None
) -> AuthenticatedSession | None:
    """Validate a cookie, enforce absolute/idle expiry and update its activity time."""
    if not token:
        return None
    current = now or datetime.now(UTC)
    row = _find_session(conn, token)
    if row is None:
        return None
    created_at = row["created_at"]
    last_seen_at = row["last_seen_at"]
    expires_at = row["expires_at"]
    revoked_at = row["revoked_at"]
    if (
        revoked_at is not None
        or not isinstance(created_at, datetime)
        or not isinstance(last_seen_at, datetime)
        or not isinstance(expires_at, datetime)
        or expires_at <= current
        or last_seen_at + timedelta(seconds=SESSION_IDLE_SECONDS) <= current
    ):
        return None
    session_id = row["id"]
    csrf_token = row["csrf_token"]
    if not isinstance(session_id, UUID) or not isinstance(csrf_token, str):
        return None
    conn.execute(
        update(owner_sessions)
        .where(owner_sessions.c.id == session_id, owner_sessions.c.revoked_at.is_(None))
        .values(last_seen_at=current)
    )
    return AuthenticatedSession(session_id, csrf_token, created_at, current, expires_at)


def revoke_session(conn: Connection, session_id: UUID, *, now: datetime | None = None) -> None:
    conn.execute(
        update(owner_sessions)
        .where(owner_sessions.c.id == session_id, owner_sessions.c.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )


def revoke_session_by_token(
    conn: Connection, token: str | None, *, now: datetime | None = None
) -> None:
    if not token:
        return
    row = _find_session(conn, token)
    if row is None or not isinstance(row["id"], UUID):
        return
    revoke_session(conn, row["id"], now=now)


def revoke_all_sessions(conn: Connection, *, now: datetime | None = None) -> None:
    conn.execute(
        update(owner_sessions)
        .where(owner_sessions.c.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
