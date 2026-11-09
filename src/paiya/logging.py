"""Small JSON-lines event logger for operational health signals."""

import json
import re
import sys
from datetime import UTC, datetime

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "capture_run_finished": frozenset(
        {
            "run_id",
            "group_id",
            "job_kind",
            "adapter_version",
            "result",
            "reason",
            "duration_ms",
            "queue_wait_ms",
            "cards_seen",
            "cards_parsed",
            "duplicate_cards_skipped",
        }
    ),
    "media_failed": frozenset({"run_id", "evidence_local_key", "reason"}),
    "disk_low": frozenset({"path_kind", "free_bytes", "min_free_bytes"}),
    "enrich_job_finished": frozenset({"job_id", "result", "reason", "duration_ms"}),
    "backup_run_finished": frozenset({"backup_run_id", "result", "reason", "duration_ms"}),
    "purge_finished": frozenset({"purge_id", "result", "pending_count"}),
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def safe_token(value: object, fallback: str = "redacted") -> str:
    """Keep caller supplied labels out of logs unless they are short internal tokens."""
    if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
        return value
    return fallback


def _validate_fields(event: str, fields: dict[str, object]) -> None:
    expected = _EVENT_FIELDS.get(event)
    if expected is None or fields.keys() != expected:
        raise ValueError(f"Invalid fields for log event {event!r}")
    for key, value in fields.items():
        if key in {
            "duration_ms",
            "queue_wait_ms",
            "cards_seen",
            "cards_parsed",
            "duplicate_cards_skipped",
            "free_bytes",
            "min_free_bytes",
            "pending_count",
        }:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Invalid numeric log field {key!r}")
        elif key == "reason":
            if value is not None and (
                not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value)
            ):
                raise ValueError("Log reasons must be short internal codes")
        elif key == "path_kind":
            if value not in {"media", "backup"}:
                raise ValueError("Invalid disk path kind")
        elif key == "job_kind":
            if value not in {"discover", "revisit", "manual_import", "enrich", "purge"}:
                raise ValueError("Invalid job kind")
        elif key == "result":
            if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
                raise ValueError("Log results must be short internal codes")
        elif not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
            raise ValueError(f"Invalid identifier log field {key!r}")


def emit_event(event: str, **fields: object) -> None:
    """Write one allow-listed operational event to stderr as a JSON line."""
    _validate_fields(event, fields)
    level = "warning" if event in {"disk_low", "media_failed"} else "info"
    if fields.get("result") == "failed":
        level = "warning"
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "event": event,
        **fields,
    }
    print(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True
    )
