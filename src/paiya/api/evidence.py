"""HTTP routes that adapt the shared fixture read services to FastAPI."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import Connection

from paiya.read.context import ReadContext
from paiya.read.errors import ReadContextRequired
from paiya.read.service import (
    read_evidence,
    read_evidence_media,
    read_group_runs,
    read_post,
    read_post_timeline,
    read_seller,
)
from paiya.settings import get_database_engine

router = APIRouter(prefix="/v1")


def require_read_context() -> ReadContext:
    """Deny by default until the server or test harness supplies trusted context."""
    raise ReadContextRequired()


# Retain the existing dependency name for A2 test harnesses and callers.
require_fixture_scope = require_read_context


def get_db_connection() -> Iterator[Connection]:
    """Yield a request connection from the process-wide engine."""
    with get_database_engine().connect() as connection:
        yield connection


Scope = Annotated[ReadContext, Depends(require_read_context)]
Db = Annotated[Connection, Depends(get_db_connection)]


@router.get("/posts/{post_id}")
def get_post(post_id: UUID, scope: Scope, conn: Db) -> dict[str, object]:
    return read_post(conn, scope, post_id)


@router.get("/sellers/{seller_id}")
def get_seller(seller_id: UUID, scope: Scope, conn: Db) -> dict[str, object]:
    return read_seller(conn, scope, seller_id)


@router.get("/posts/{post_id}/timeline")
def get_post_timeline(
    post_id: UUID,
    scope: Scope,
    conn: Db,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        return read_post_timeline(conn, scope, post_id, limit=limit, cursor=cursor)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid cursor") from error


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: UUID, scope: Scope, conn: Db) -> dict[str, object]:
    return read_evidence(conn, scope, evidence_id)


@router.get("/evidence/{evidence_id}/media")
def get_evidence_media(evidence_id: UUID, scope: Scope, conn: Db) -> Response:
    media = read_evidence_media(conn, scope, evidence_id)
    return Response(
        content=media.data,
        media_type=media.mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/groups/{group_id}/runs")
def get_group_runs(
    group_id: UUID,
    scope: Scope,
    conn: Db,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        return read_group_runs(conn, scope, group_id, limit=limit, cursor=cursor)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid cursor") from error
