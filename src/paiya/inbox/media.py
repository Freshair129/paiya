"""Recover media written by an interrupted Collector transaction."""

import os
import re
from pathlib import Path

from sqlalchemy import Connection, select

from paiya.store.media import lock_media_asset_for_transaction
from paiya.store.tables import media_assets

_MEDIA_NAME = re.compile(r"([0-9a-f]{64})\.(webp|jpg)")


def _staging_path(journal: Path, destination: Path) -> Path:
    return destination.with_name(f"{journal.name}-{destination.name}.tmp")


def record_media_write(journal: Path, destination: Path) -> Path:
    """Publish a complete journal entry before any bytes reach the media store."""
    marker = journal / destination.name
    if not marker.exists():
        with marker.open("xb") as stream:
            os.fsync(stream.fileno())
    return _staging_path(journal, destination)


def recover_media(conn: Connection, media: Path, journal: Path) -> None:
    """Remove only recorded uncommitted files; leave committed shared assets in place."""
    if not journal.exists():
        return
    if journal.is_symlink() or journal.is_junction() or not journal.is_dir():
        raise OSError("Invalid Collector media journal")
    for marker in sorted(journal.iterdir()):
        match = _MEDIA_NAME.fullmatch(marker.name)
        if not match or marker.is_symlink() or not marker.is_file():
            raise OSError("Invalid Collector media journal entry")
        digest = match.group(1)
        with conn.begin():
            lock_media_asset_for_transaction(conn, digest)
            destination = media / "assets" / marker.name
            _staging_path(journal, destination).unlink(missing_ok=True)
            stored = conn.execute(
                select(media_assets.c.id).where(media_assets.c.storage_key == digest)
            ).first()
            if stored is None:
                destination.unlink(missing_ok=True)
            marker.unlink()
    journal.rmdir()
