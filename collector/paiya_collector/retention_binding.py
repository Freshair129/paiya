"""Record a local operator's review of a cleanup runner; never perform cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from paiya_collector.state import ensure_no_reparse_components


def database_identity(database: str) -> str:
    parts = urlsplit(database)
    if not parts.scheme:
        raise ValueError("invalid database URL")
    if parts.query or parts.fragment:
        raise ValueError("database URL query or fragment cannot be safely bound")
    if parts.scheme.startswith("postgres") and not (
        parts.hostname and parts.port and parts.username and parts.path.strip("/")
    ):
        raise ValueError("database URL requires explicit user, host, port and database")
    credentials, separator, host = parts.netloc.rpartition("@")
    authority = f"{credentials.split(':', 1)[0]}@{host}" if separator else parts.netloc
    return f"{parts.scheme}://{authority}{parts.path}"


def write_reviewed_binding(
    *, data_root: Path, runner: Path, media: Path, database: str, confirmed: bool
) -> None:
    if not confirmed:
        raise ValueError("cleanup runner review is required")
    root = data_root.absolute()
    runner = runner.absolute()
    media = media.absolute()
    binding_path = root / "b2-ops" / "retention-binding.json"
    for path in (root, runner, media, binding_path):
        ensure_no_reparse_components(path, label="retention binding path")
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_reparse_components(binding_path, label="retention binding path")
    identity = database_identity(database)
    binding = {
        "version": 1,
        "runner": str(runner),
        "sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "inbox": str(root / "live-inbox"),
        "state": str(root / "live-state"),
        "media": str(media),
        "database": identity,
    }
    with binding_path.open("x", encoding="utf-8") as handle:
        json.dump(binding, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--confirm-reviewed", action="store_true")
    args = parser.parse_args()
    write_reviewed_binding(
        data_root=args.data_root,
        runner=args.runner,
        media=args.media,
        database=os.environ["PAIYA_DATABASE_URL"],
        confirmed=args.confirm_reviewed,
    )
    print("Local reviewed retention binding recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
