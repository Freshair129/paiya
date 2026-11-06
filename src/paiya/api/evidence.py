"""Read-only fixture evidence routes. All scope is server-injected, never from headers."""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy import Connection, create_engine, select

from paiya.settings import get_database_url
from paiya.store.fixture_scope import FixtureScope, verify_fixture_scope
from paiya.store.media import media_path
from paiya.store.tables import evidence, media_assets, observations, posts

router = APIRouter(prefix="/v1")


def require_fixture_scope() -> FixtureScope:
    """Deny by default; test harness installs a trusted dependency override."""
    raise HTTPException(status_code=403, detail="Fixture access context required")


def get_db_connection() -> Iterator[Connection]:
    engine = create_engine(get_database_url())
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


Scope = Annotated[FixtureScope, Depends(require_fixture_scope)]
Db = Annotated[Connection, Depends(get_db_connection)]


def _verify(conn: Connection, scope: FixtureScope, purpose: str = "read") -> None:
    try:
        verify_fixture_scope(conn, scope, purpose)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Fixture grant unavailable") from None


def _evidence_row(conn: Connection, scope: FixtureScope, evidence_id: UUID) -> Any:
    row = (
        conn.execute(
            select(
                evidence.c.id,
                evidence.c.local_key,
                evidence.c.observation_id,
                evidence.c.post_id,
                evidence.c.role,
                evidence.c.source_span,
                evidence.c.media_state,
                evidence.c.media_reason,
                media_assets.c.sha256,
                media_assets.c.mime,
                media_assets.c.bytes,
                media_assets.c.width,
                media_assets.c.height,
            )
            .join(observations, evidence.c.observation_id == observations.c.id)
            .outerjoin(media_assets, evidence.c.media_asset_id == media_assets.c.id)
            .where(
                evidence.c.id == evidence_id,
                observations.c.group_id == scope.group_id,
                observations.c.grant_id == scope.grant_id,
                evidence.c.grant_id == scope.grant_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return row


@router.get("/posts/{post_id}")
def get_post(post_id: UUID, scope: Scope, conn: Db) -> dict[str, Any]:
    _verify(conn, scope)
    post = (
        conn.execute(
            select(posts.c.id, posts.c.permalink, posts.c.source_post_key).where(
                posts.c.id == post_id, posts.c.group_id == scope.group_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    observed = (
        conn.execute(
            select(
                observations.c.id,
                observations.c.capture_id,
                observations.c.observed_at,
                observations.c.access,
                observations.c.activity,
                observations.c.normalized_json,
            )
            .where(
                observations.c.post_id == post_id,
                observations.c.group_id == scope.group_id,
                observations.c.grant_id == scope.grant_id,
            )
            .order_by(observations.c.observed_at, observations.c.id)
        )
        .mappings()
        .all()
    )
    if not observed:
        raise HTTPException(status_code=404, detail="Post not found")
    evidence_rows = (
        conn.execute(
            select(evidence.c.id, evidence.c.observation_id).where(
                evidence.c.post_id == post_id,
                evidence.c.grant_id == scope.grant_id,
            )
        )
        .mappings()
        .all()
    )
    result = {
        "id": post["id"],
        "permalink": post["permalink"],
        "source_post_key": post["source_post_key"],
        "observations": [
            {
                "id": row["id"],
                "capture_id": row["capture_id"],
                "observed_at": row["observed_at"],
                "access": row["access"],
                "activity": row["activity"],
                "items": row["normalized_json"]["payload"].get("items", []),
                "evidence_ids": [
                    item["id"] for item in evidence_rows if item["observation_id"] == row["id"]
                ],
            }
            for row in observed
        ],
    }
    return cast(dict[str, Any], jsonable_encoder(result))


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: UUID, scope: Scope, conn: Db) -> dict[str, Any]:
    _verify(conn, scope)
    row = _evidence_row(conn, scope, evidence_id)
    return cast(dict[str, Any], jsonable_encoder(dict(row)))


@router.get("/evidence/{evidence_id}/media")
def get_evidence_media(
    evidence_id: UUID,
    scope: Scope,
    conn: Db,
) -> Response:
    _verify(conn, scope, "media")
    row = _evidence_row(conn, scope, evidence_id)
    if row["media_state"] != "captured" or row["sha256"] is None:
        raise HTTPException(status_code=404, detail="No captured image for this evidence")
    # Root comes from the trusted server configuration, not the client request.
    configured_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not configured_root:
        raise HTTPException(status_code=503, detail="Fixture media root not configured")
    root = Path(configured_root).resolve()
    file_path = media_path(root, row["sha256"], row["mime"])
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Captured image missing from storage")
    data = file_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise HTTPException(status_code=409, detail="Captured image integrity failure")
    return Response(content=data, media_type=row["mime"])
