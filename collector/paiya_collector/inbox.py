from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path


def publish_bundle(
    inbox_root: Path,
    run_document: dict[str, object],
    envelopes: list[dict[str, object]],
    asset_bytes: dict[str, bytes],
    *,
    before_rename: Callable[[Path, Path], None] | None = None,
) -> Path:
    run_id = str(run_document["run_id"])
    incoming = inbox_root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = incoming / f".{run_id}.tmp"
    destination = incoming / run_id
    if destination.exists():
        raise FileExistsError(f"run_id is already published: {run_id}")
    if temporary.exists():
        raise FileExistsError(f"unpublished temporary run already exists: {run_id}")

    temporary.mkdir()
    envelope_dir = temporary / "envelopes"
    asset_dir = temporary / "assets"
    envelope_dir.mkdir()
    asset_dir.mkdir()
    _write_json(
        temporary / ".retention.json",
        {
            "run_id": run_id,
            "group_key": run_document.get("group_key"),
            "created_at": run_document.get("started_at"),
        },
    )
    _write_json(temporary / "run.json", run_document)

    pending_assets: dict[str, bytes] = {}
    for envelope in envelopes:
        capture_id = str(envelope["capture_id"])
        _write_json(envelope_dir / f"{capture_id}.json", envelope)
        metadata = envelope.get("assets", {})
        if isinstance(metadata, dict):
            for asset in metadata.values():
                if isinstance(asset, dict):
                    staging_key = asset.get("staging_key")
                    if isinstance(staging_key, str) and staging_key in asset_bytes:
                        pending_assets[staging_key] = asset_bytes[staging_key]
    for staging_key, data in pending_assets.items():
        (asset_dir / staging_key).write_bytes(data)

    # READY is the final file created in the hidden directory.
    (temporary / ".retention.json").unlink()
    with (temporary / "READY").open("xb"):
        pass
    if before_rename is not None:
        before_rename(temporary, destination)
    os.rename(temporary, destination)
    return destination.resolve()


def visible_ready_runs(incoming: Path) -> list[Path]:
    if not incoming.exists():
        return []
    return sorted(
        path
        for path in incoming.iterdir()
        if path.is_dir() and not path.name.startswith(".") and (path / "READY").is_file()
    )


def _write_json(path: Path, document: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
