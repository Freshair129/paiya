"""Idempotent seeding for the synthetic mobile application walkthrough."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, select

from paiya.groups.errors import GroupError
from paiya.groups.service import add_group, resume, set_interval
from paiya.ingest.service import ingest_fixture
from paiya.store.fixture_scope import (
    FixtureScope,
    seed_fixture_scope,
    verify_fixture_scope,
)
from paiya.store.tables import groups, posts
from paiya.watches.service import add_watch

DEMO_GROUP_KEY = "synthetic-demo"
BUNDLE_POST_KEY = "demo-multi-item-bundle"
IMAGE_FILES = {
    "demo-rtx3060": "rtx3060.webp",
    "demo-multi-item": "multi-item.webp",
}
ENVELOPE_FILES = (
    "01-rtx3060-captured.json",
    "02-multi-item-open.json",
    "03-multi-item-reserved.json",
    "04-capture-failed.json",
)


def _ensure_demo_scope(engine: Engine, now: datetime) -> FixtureScope:
    try:
        with engine.begin() as conn:
            add_group(conn, now, DEMO_GROUP_KEY, 30)
    except GroupError as error:
        if error.code != "group_already_exists":
            raise

    with engine.connect() as conn:
        row = (
            conn.execute(
                select(groups.c.id, groups.c.enabled, groups.c.poll_minutes).where(
                    groups.c.source == "fixture",
                    groups.c.group_key == DEMO_GROUP_KEY,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RuntimeError("Could not create the synthetic-demo fixture group")

    group_id = cast(UUID, row["id"])
    if row["poll_minutes"] != 30:
        with engine.begin() as conn:
            set_interval(conn, now, group_id, 30)
    if not row["enabled"]:
        with engine.begin() as conn:
            resume(conn, now, group_id)

    scope = seed_fixture_scope(engine, DEMO_GROUP_KEY)
    try:
        with engine.connect() as conn:
            for purpose in ("read", "capture", "media"):
                verify_fixture_scope(conn, scope, purpose)
    except PermissionError as error:
        raise RuntimeError(
            "synthetic-demo requires an active fixture grant with read, capture, and media scopes"
        ) from error
    return scope


def _load_envelope(path: Path, image_dir: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    document = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    assets: dict[str, bytes] = {}
    for meta in cast(dict[str, dict[str, Any]], document["assets"]).values():
        staging_key = cast(str, meta["staging_key"])
        image_name = IMAGE_FILES.get(staging_key)
        if image_name is None:
            raise ValueError(f"No synthetic demo image is mapped for staging key {staging_key!r}")
        image_path = image_dir / image_name
        assets[staging_key] = image_path.read_bytes()
    return document, assets


def seed_demo(engine: Engine) -> dict[str, object]:
    """Create the demo group, ingest fixed synthetic envelopes, and watch the bundle post."""
    configured_media_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not configured_media_root:
        raise RuntimeError("PAIYA_FIXTURE_MEDIA_ROOT is required for demo seed")
    media_root = Path(configured_media_root).expanduser()
    if media_root.exists() and not media_root.is_dir():
        raise RuntimeError("PAIYA_FIXTURE_MEDIA_ROOT must name a directory")

    now = datetime.now(UTC)
    scope = _ensure_demo_scope(engine, now)
    fixture_dir = Path(__file__).resolve().parents[2] / "fixtures" / "demo"
    image_dir = fixture_dir / "images"
    observation_ids: list[str] = []
    for filename in ENVELOPE_FILES:
        document, assets = _load_envelope(fixture_dir / "envelopes" / filename, image_dir)
        observation_id = ingest_fixture(engine, scope, document, assets, media_root)
        observation_ids.append(str(observation_id))

    with engine.begin() as conn:
        bundle_post_id = conn.execute(
            select(posts.c.id).where(
                posts.c.group_id == scope.group_id,
                posts.c.source_post_key == BUNDLE_POST_KEY,
            )
        ).scalar_one_or_none()
        if bundle_post_id is None:
            raise RuntimeError("The synthetic multi-item demo post was not ingested")
        watch = add_watch(conn, now, cast(UUID, bundle_post_id))

    return {
        "group_id": str(scope.group_id),
        "group_key": DEMO_GROUP_KEY,
        "observation_ids": observation_ids,
        "watch": watch,
    }
