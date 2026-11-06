"""HTTP routes that adapt the shared fixture read services to FastAPI."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import Connection

from paiya.read.context import ReadContext
from paiya.read.errors import ReadContextRequired
from paiya.read.service import read_evidence, read_evidence_media, read_post
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
