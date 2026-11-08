"""Read and validate the append-only purge command log."""

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from paiya.purge.errors import PurgeError

_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")
_REQUIRED_KEYS = {"id", "scope_kind", "scope_key", "requested_at"}


@dataclass(frozen=True)
class PurgeLogEntry:
    """One validated command recorded before its database changes."""

    id: UUID
    scope_kind: str
    scope_key: str
    requested_at: datetime

    @property
    def scope(self) -> str:
        return f"{self.scope_kind}:{self.scope_key}"


def _reject_constant(_value: str) -> None:
    raise ValueError("JSON constants must be standards-compliant")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_purge_log_line(line: bytes) -> PurgeLogEntry:
    """Parse one JSONL record without exposing its contents in errors."""
    try:
        value = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("record must be a JSON object")
        if set(value) != _REQUIRED_KEYS:
            raise ValueError("record has unexpected or missing fields")

        raw_id = value.get("id")
        raw_kind = value.get("scope_kind")
        raw_key = value.get("scope_key")
        raw_requested_at = value.get("requested_at")
        if not isinstance(raw_id, str):
            raise ValueError("id must be a string")
        if not isinstance(raw_kind, str):
            raise ValueError("scope_kind must be a string")
        if not isinstance(raw_key, str):
            raise ValueError("scope_key must be a string")
        if not isinstance(raw_requested_at, str):
            raise ValueError("record fields must be strings")
        if raw_kind not in {"grant", "post"}:
            raise ValueError("unknown scope kind")

        purge_id = UUID(raw_id)
        scope_id = UUID(raw_key)
        if str(purge_id) != raw_id.casefold() or str(scope_id) != raw_key.casefold():
            raise ValueError("UUID fields must use the standard UUID form")

        if _UTC_TIMESTAMP.fullmatch(raw_requested_at) is None:
            raise ValueError("requested_at must be UTC")
        requested_at = datetime.fromisoformat(raw_requested_at.replace("Z", "+00:00"))
        if requested_at.utcoffset() != timedelta(0):
            raise ValueError("requested_at must be UTC")
        return PurgeLogEntry(
            id=purge_id,
            scope_kind=raw_kind,
            scope_key=str(scope_id),
            requested_at=requested_at.astimezone(UTC),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PurgeError("purge_log_invalid", "The purge log contains an invalid line") from error


def iter_purge_log(path: Path, *, missing_ok: bool = False) -> Iterator[PurgeLogEntry]:
    """Yield validated records in order; an invalid record stops iteration."""
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        if missing_ok:
            return
        raise PurgeError("purge_log_invalid", "The purge log could not be read") from None
    except OSError as error:
        raise PurgeError("purge_log_invalid", "The purge log could not be read") from error

    try:
        with stream:
            for line in stream:
                yield parse_purge_log_line(line)
    except OSError as error:
        raise PurgeError("purge_log_invalid", "The purge log could not be read") from error


def ensure_unique_purge_ids(entries: Iterable[PurgeLogEntry]) -> list[PurgeLogEntry]:
    """Reject an ID reused with different command details while preserving order."""
    seen: dict[UUID, PurgeLogEntry] = {}
    result: list[PurgeLogEntry] = []
    for entry in entries:
        previous = seen.get(entry.id)
        if previous is not None and previous != entry:
            raise PurgeError("purge_log_invalid", "The purge log reuses an ID inconsistently")
        seen[entry.id] = entry
        result.append(entry)
    return result
