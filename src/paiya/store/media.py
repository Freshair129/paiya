"""Content-addressed fixture media with bounded, atomic file writes."""

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

MAX_MEDIA_BYTES = 5 * 1024 * 1024
STAGING_KEY = re.compile(r"^[a-zA-Z0-9_-]{1,200}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_media(staging_key: str, meta: Mapping[str, object], data: bytes) -> str:
    """Reject a forged staging key, digest, length, media type, or image signature."""
    if not STAGING_KEY.fullmatch(staging_key):
        raise ValueError("Invalid staging_key (no path segments or traversal)")
    size = len(data)
    if size == 0 or size > MAX_MEDIA_BYTES or size != meta["bytes"]:
        raise ValueError("Image size mismatch or exceeds 5 MiB")
    claimed = meta["sha256"]
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(claimed, str) or not SHA256.fullmatch(claimed) or digest != claimed:
        raise ValueError("Image SHA-256 mismatch")
    mime = meta["mime"]
    if mime == "image/webp":
        if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise ValueError("Invalid WEBP signature")
    elif mime == "image/jpeg":
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG signature")
    else:
        raise ValueError("Unsupported media type")
    return digest


def media_path(root: Path, sha256: str, mime: str) -> Path:
    """Resolve a stored content hash, never an externally supplied arbitrary path."""
    if not SHA256.fullmatch(sha256):
        raise ValueError("Invalid storage hash")
    ext = {"image/webp": ".webp", "image/jpeg": ".jpg"}.get(mime)
    if ext is None:
        raise ValueError("Invalid media MIME")
    return root / "assets" / (sha256 + ext)


def write_media(root: Path, meta: Mapping[str, object], data: bytes, key: str) -> Path:
    digest = validate_media(key, meta, data)
    destination = media_path(root, digest, str(meta["mime"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing media asset failed integrity check")
        return destination
    # Same filesystem, so replace is atomic; orphans from DB failure can be cleaned.
    fd, temp_name = tempfile.mkstemp(dir=destination.parent, prefix=".paiya-staged-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return destination


def cleanup_orphans(root: Path, referenced_names: set[str], grace_seconds: int = 3600) -> int:
    """Remove unreferenced, old staged assets only after the DB has recovered."""
    if grace_seconds < 0:
        raise ValueError("grace_seconds must not be negative")
    folder = root / "assets"
    if not folder.exists():
        return 0
    deleted = 0
    cutoff = time.time() - grace_seconds
    for candidate in folder.iterdir():
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if candidate.name in referenced_names or candidate.stat().st_mtime >= cutoff:
            continue
        if candidate.name.startswith(".paiya-staged-") or re.fullmatch(
            r"[0-9a-f]{64}\.(webp|jpg)", candidate.name
        ):
            candidate.unlink()
            deleted += 1
    return deleted
