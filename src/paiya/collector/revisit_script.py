"""Local-only adapter for synthetic permalink revisit scripts."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from paiya.collector.errors import AdapterError


@dataclass(frozen=True)
class RevisitVisit:
    source_post_key: str
    at: datetime
    access: str
    envelope: dict[str, Any] | None


class RevisitScriptAdapter:
    """Read one exact scheduled visit and its local envelope, with no network path."""

    def __init__(self, script_path: Path, schema_path: Path | None = None) -> None:
        self.script_path = script_path.resolve()
        self.schema_path = schema_path or (
            Path(__file__).resolve().parents[3] / "fixtures/revisits/script.schema.json"
        )

    def read_visit(
        self, group_key: str, source_post_key: str, scheduled_for: datetime
    ) -> RevisitVisit:
        try:
            document = json.loads(self.script_path.read_text(encoding="utf-8"))
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdapterError(
                "adapter_error", "Could not read the local revisit script"
            ) from error
        validator = Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        failures = list(validator.iter_errors(document))
        if failures:
            raise AdapterError("adapter_error", f"Invalid revisit script: {failures[0].message}")
        if document["group_key"] != group_key:
            raise AdapterError("adapter_error", "Revisit script group_key does not match the job")
        if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
            raise ValueError("scheduled_for must be timezone-aware")
        wanted_at = scheduled_for.astimezone(UTC)
        matches = [
            visit
            for visit in document["visits"]
            if visit["source_post_key"] == source_post_key and _parse_utc(visit["at"]) == wanted_at
        ]
        if len(matches) != 1:
            raise AdapterError(
                "adapter_error",
                "Revisit script must contain exactly one visit for the requested post and slot",
            )
        raw = matches[0]
        envelope = self._read_envelope(raw["envelope"]) if raw["envelope"] is not None else None
        if envelope is not None:
            payload = envelope.get("payload")
            if not isinstance(payload, dict):
                raise AdapterError("adapter_error", "Envelope payload must be an object")
            if payload.get("group_key") != group_key:
                raise AdapterError("adapter_error", "Envelope group_key does not match the job")
            if payload.get("source_post_key") != source_post_key or payload.get("access") != "ok":
                raise AdapterError("adapter_error", "Envelope does not match its readable visit")
        return RevisitVisit(
            source_post_key=source_post_key,
            at=_parse_utc(raw["at"]),
            access=raw["access"],
            envelope=envelope,
        )

    def _read_envelope(self, name: str) -> dict[str, Any]:
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise AdapterError(
                "adapter_error", "Envelope path must stay inside its revisit scenario"
            )
        root = self.script_path.parent.resolve()
        envelope_path = (root / Path(*relative.parts)).resolve()
        if not envelope_path.is_relative_to(root):
            raise AdapterError(
                "adapter_error", "Envelope path must stay inside its revisit scenario"
            )
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdapterError(
                "adapter_error", "Could not read a local revisit envelope"
            ) from error
        if not isinstance(envelope, dict):
            raise AdapterError("adapter_error", "Envelope must be a JSON object")
        return envelope


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError("adapter_error", "Revisit at must include a UTC timezone")
    return parsed.astimezone(UTC)
