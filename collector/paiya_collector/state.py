from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


def state_path(state_dir: Path, group_key: str) -> Path:
    safe = re.fullmatch(r"[A-Za-z0-9._-]{1,160}", group_key)
    filename = f"{group_key}.json" if safe else "group-state.json"
    return state_dir / filename


def ensure_no_reparse_components(path: Path, *, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink() or current.is_junction():
            raise ValueError(f"{label} must not traverse a symlink or junction")


def load_state(path: Path, group_key: str) -> dict[str, object]:
    ensure_no_reparse_components(path, label="state path")
    if not path.exists():
        return {
            "group_key": group_key,
            "checkpoint": None,
            "published_keys": [],
            "published_key_seen_at": {},
            "published_runs": {},
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read local checkpoint state: {exc}") from exc
    if not isinstance(state, dict) or state.get("group_key") != group_key:
        raise ValueError("local checkpoint state does not match the configured group")
    if not isinstance(state.get("published_keys"), list):
        raise ValueError("local checkpoint state has invalid published_keys")
    state.setdefault("published_key_seen_at", {})
    state.setdefault("published_runs", {})
    if not isinstance(state["published_key_seen_at"], dict):
        raise ValueError("local checkpoint state has invalid published_key_seen_at")
    if not isinstance(state["published_runs"], dict):
        raise ValueError("local checkpoint state has invalid published_runs")
    if "checkpoint" not in state:
        raise ValueError("local checkpoint state is missing checkpoint")
    return state


def prune_expired_state(
    path: Path,
    *,
    group_key: str,
    retention_days: int,
    now: datetime,
    run_id: str,
) -> dict[str, object]:
    if not 1 <= retention_days <= 7:
        raise ValueError("state retention must be between 1 and 7 days")
    current = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    cutoff = current - timedelta(days=retention_days)
    state = load_state(path, group_key)
    next_state = copy.deepcopy(state)

    raw_keys = next_state["published_keys"]
    raw_key_times = next_state["published_key_seen_at"]
    raw_runs = next_state["published_runs"]
    if not isinstance(raw_keys, list) or not isinstance(raw_key_times, dict):
        raise ValueError("local checkpoint state has invalid retention metadata")
    if not isinstance(raw_runs, dict):
        raise ValueError("local checkpoint state has invalid published_runs")

    key_times = raw_key_times
    kept_keys: list[str] = []
    kept_key_times: dict[str, str] = {}
    seen_keys: set[str] = set()
    for key in raw_keys:
        if not isinstance(key, str) or key in seen_keys:
            continue
        seen_keys.add(key)
        published_at = _parse_timestamp(key_times.get(key))
        if published_at is not None and cutoff < published_at <= current:
            kept_keys.append(key)
            kept_key_times[key] = str(key_times[key])

    previous_runs = raw_runs
    kept_runs: dict[str, object] = {}
    for old_run_id, entry in previous_runs.items():
        published_at = (
            _parse_timestamp(entry.get("published_at")) if isinstance(entry, dict) else None
        )
        if published_at is not None and cutoff < published_at <= current:
            kept_runs[old_run_id] = entry

    expired_keys = len(raw_keys) - len(kept_keys)
    expired_runs = len(previous_runs) - len(kept_runs)
    checkpoint = next_state["checkpoint"]
    expired_checkpoint_keys = 0
    if isinstance(checkpoint, dict):
        advanced_at = _parse_timestamp(checkpoint.get("advanced_at"))
        if advanced_at is None or advanced_at <= cutoff or advanced_at > current:
            top_keys = checkpoint.get("top_keys", [])
            if isinstance(top_keys, list):
                expired_checkpoint_keys = len(top_keys)
            checkpoint["top_keys"] = []
    changed = (
        next_state["published_keys"] != kept_keys
        or key_times != kept_key_times
        or previous_runs != kept_runs
        or next_state["checkpoint"] != state["checkpoint"]
    )
    next_state["published_keys"] = kept_keys
    next_state["published_key_seen_at"] = kept_key_times
    next_state["published_runs"] = kept_runs
    if changed:
        write_state(path, next_state, str(uuid.UUID(run_id)))
    return {
        "expired_published_keys": expired_keys,
        "expired_published_runs": expired_runs,
        "expired_checkpoint_keys": expired_checkpoint_keys,
        "checkpoint_preserved": _same_checkpoint_cursor(
            next_state["checkpoint"], state["checkpoint"]
        ),
    }


def write_state(path: Path, state: dict[str, object], run_id: str) -> None:
    ensure_no_reparse_components(path, label="state path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{run_id}.tmp")
    if temporary.exists():
        raise FileExistsError(f"checkpoint temporary file already exists: {temporary.name}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _same_checkpoint_cursor(left: object, right: object) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left == right
    return (left.get("run_id"), left.get("advanced_at")) == (
        right.get("run_id"),
        right.get("advanced_at"),
    )
