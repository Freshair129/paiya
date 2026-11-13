"""Read-only validation for Collector inbox run directories."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from paiya.ingest.validation import SCHEMA, validate_observation
from paiya.store.media import MAX_MEDIA_BYTES

_RUN_FIELDS = (
    "run_id",
    "group_key",
    "grant_id",
    "job_kind",
    "adapter_version",
    "started_at",
    "ended_at",
    "session_result",
    "sort_result",
    "coverage",
    "partial_reasons",
    "stop_reason",
    "cards_seen",
    "cards_parsed",
    "scroll_steps",
    "error_code",
)
_RUN_ENUMS: dict[str, set[str]] = {
    "job_kind": {"discover", "revisit"},
    "session_result": {"ok", "required", "unknown"},
    "sort_result": {"verified_latest", "unverified"},
    "coverage": {"complete", "partial", "none"},
    "stop_reason": {
        "overlap_reached",
        "feed_end",
        "time_budget_exhausted",
        "scroll_budget_exhausted",
        "access_blocked",
    },
}
_PARTIAL_REASONS = {
    "sort_unverified",
    "budget_before_overlap",
    "feed_stopped_before_overlap",
    "parse_failures",
}
_ERROR_CODES = {
    "session_required",
    "challenge_required",
    "permission_denied",
    "grant_unavailable",
    "adapter_error",
    "post_unavailable",
}
_TEXT_FIELDS = {
    "source_text",
    "display_name",
    "model",
    "condition_text",
    "source_status_label",
    "text",
    "quote",
    "media_reason",
}
_HTML_MARKUP = re.compile(r"<!--|<!DOCTYPE\b|<\s*/?\s*[A-Za-z][^>]*>", re.IGNORECASE)
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cookie_c_user", re.compile(r"\bc_user\s*=\s*['\"]?[^\s;,\"'<>]+", re.IGNORECASE)),
    ("cookie_xs", re.compile(r"\bxs\s*=\s*['\"]?[^\s;,\"'<>]+", re.IGNORECASE)),
    ("cookie_datr", re.compile(r"\bdatr\s*=\s*['\"]?[^\s;,\"'<>]+", re.IGNORECASE)),
    ("cookie_assignment", re.compile(r"\bcookie\s*=", re.IGNORECASE)),
    ("cookie_header", re.compile(r"\bcookie\s*:", re.IGNORECASE)),
    ("sessionid", re.compile(r"\bsession(?:[_-]?id|[_-]?token)\b", re.IGNORECASE)),
    ("set_cookie", re.compile(r"\bset-cookie\b", re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bbearer\s+[^\s,;\"']+", re.IGNORECASE)),
    ("password", re.compile(r"\bpassword\b", re.IGNORECASE)),
)
_EXPECTED_TOP_LEVEL = {"run.json", "envelopes", "assets", "READY"}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:c_user|xs|datr)\s*=\s*['\"]?[^\s;,\"'<>]+", re.IGNORECASE),
    re.compile(r"\bcookie\s*=\s*[^\s;,\"'<>]+", re.IGNORECASE),
    re.compile(r"\bcookie\s*:\s*[^\r\n]+", re.IGNORECASE),
    re.compile(r"\bset-cookie\s*:\s*[^\r\n]+", re.IGNORECASE),
    re.compile(r"\bsession(?:[_-]?id|[_-]?token)\s*[:=]\s*[^\s;,]+", re.IGNORECASE),
    re.compile(r"\bbearer\s+[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"\bpassword\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
)


@dataclass(frozen=True)
class ValidatedRun:
    summary: dict[str, Any]
    envelopes: list[dict[str, Any]]
    assets: dict[str, bytes]


def validate_inbox(run_dir: Path) -> dict[str, object]:
    """Return a JSON-safe report without changing the run directory."""
    return read_inbox(run_dir)[0]


def read_inbox(run_dir: Path) -> tuple[dict[str, object], ValidatedRun | None]:
    """Return the report and the exact data validated, without reopening its files."""
    problems: list[dict[str, str]] = []
    problem_keys: set[tuple[str, str, str]] = set()

    def add_problem(path: str, code: str, message: str) -> None:
        safe_path = _sanitize_path(path)
        key = (safe_path, code, message)
        if key not in problem_keys:
            problem_keys.add(key)
            problems.append({"path": safe_path, "code": code, "message": message})

    raw_root = Path(run_dir)
    if raw_root.is_symlink() or raw_root.is_junction():
        add_problem(".", "run_dir_symlink", "Run directory must not be a symlink")
        return _report(None, 0, problems), None
    try:
        root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError):
        add_problem(".", "run_dir_unavailable", "Run directory is unavailable")
        return _report(None, 0, problems), None
    if not root.is_dir():
        add_problem(".", "run_dir_invalid", "Run path must be a directory")
        return _report(None, 0, problems), None
    if root.name.startswith("."):
        add_problem(".", "layout_hidden", "Names starting with a dot are not allowed")

    files, directories, symlinks, top_level = _walk_tree(root, add_problem)
    for entry in top_level:
        if entry.name not in _EXPECTED_TOP_LEVEL:
            add_problem(
                _relative_path(root, entry), "layout_unexpected", "Unexpected top-level entry"
            )

    run_file = root / "run.json"
    envelope_dir = root / "envelopes"
    assets_dir = root / "assets"
    ready_file = root / "READY"

    if run_file not in files:
        add_problem("run.json", "layout_missing_run", "run.json must be a regular file")
    if envelope_dir not in directories:
        add_problem("envelopes", "layout_missing_envelopes", "envelopes must be a directory")
    if assets_dir not in directories:
        add_problem("assets", "layout_missing_assets", "assets must be a directory")
    if ready_file not in files:
        add_problem("READY", "layout_ready_missing", "READY must be present as a regular file")
    else:
        try:
            if ready_file.stat().st_size != 0:
                add_problem("READY", "layout_ready_not_empty", "READY must be empty")
        except OSError:
            add_problem("READY", "layout_ready_unreadable", "READY could not be checked")

    contents: dict[Path, bytes] = {}
    parsed_json: dict[Path, Any] = {}
    invalid_json: set[Path] = set()
    unreadable: set[Path] = set()
    for file_path in files:
        try:
            if file_path.is_relative_to(assets_dir) and file_path.stat().st_size > MAX_MEDIA_BYTES:
                add_problem(
                    _relative_path(root, file_path), "asset_too_large", "Asset exceeds 5 MiB"
                )
                continue
            data = file_path.read_bytes()
        except OSError:
            unreadable.add(file_path)
            add_problem(
                _relative_path(root, file_path),
                "file_unreadable",
                "File could not be read for validation",
            )
            continue
        contents[file_path] = data
        if file_path.suffix.lower() == ".json":
            try:
                document = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid_json.add(file_path)
                _scan_raw_secrets(data, _relative_path(root, file_path), add_problem)
            else:
                parsed_json[file_path] = document
                _scan_json(document, _relative_path(root, file_path), add_problem)
        else:
            _scan_raw_secrets(data, _relative_path(root, file_path), add_problem)

    run_id: str | None = None
    run_document: dict[str, Any] | None = None
    if run_file in parsed_json:
        candidate = parsed_json[run_file]
        if isinstance(candidate, dict):
            run_document = candidate
            run_id_value = candidate.get("run_id")
            if _is_uuid(run_id_value):
                run_id = run_id_value
        else:
            add_problem("run.json", "run_json_type", "run.json must contain a JSON object")
    elif run_file in invalid_json:
        add_problem("run.json", "run_json_invalid", "run.json is not valid UTF-8 JSON")

    run_times = _validate_run_document(run_document, root.name, add_problem)

    if envelope_dir in directories:
        for child in sorted(envelope_dir.iterdir(), key=lambda path: path.name):
            rel = _relative_path(root, child)
            if child in symlinks:
                continue
            if child.is_dir():
                add_problem(rel, "envelope_entry_invalid", "Envelope entries must be JSON files")
            elif child.is_file() and child.suffix.lower() != ".json":
                add_problem(rel, "envelope_entry_invalid", "Envelope entries must use .json files")

    envelope_files = sorted(
        (path for path in files if path.parent == envelope_dir and path.suffix.lower() == ".json"),
        key=lambda path: path.name,
    )
    duplicate_ids: set[str] = set()
    seen_capture_ids: set[str] = set()
    referenced_assets: set[Path] = set()
    schema: dict[str, Any] | None = None
    if envelope_files:
        try:
            loaded_schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            add_problem("envelopes", "schema_unavailable", "Observation v1 schema is unavailable")
        else:
            schema = loaded_schema
    schema_validator = Draft202012Validator(schema) if schema is not None else None

    for envelope_file in envelope_files:
        rel = _relative_path(root, envelope_file)
        if envelope_file in invalid_json or envelope_file not in parsed_json:
            add_problem(rel, "envelope_json_invalid", "Envelope is not valid UTF-8 JSON")
            continue
        envelope = parsed_json[envelope_file]
        if not isinstance(envelope, dict):
            add_problem(rel, "envelope_json_type", "Envelope must contain a JSON object")
            continue

        schema_errors: list[Any] = []
        if schema_validator is not None:
            schema_errors = sorted(
                schema_validator.iter_errors(envelope),
                key=lambda error: (
                    tuple(str(part) for part in error.absolute_path),
                    str(error.validator),
                ),
            )
            for error in schema_errors:
                field_path = _field_path(rel, list(error.absolute_path))
                add_problem(
                    field_path,
                    "envelope_schema",
                    f"Observation v1 schema validation failed ({error.validator})",
                )
        if not schema_errors:
            try:
                validate_observation(envelope)
            except (ValueError, KeyError, TypeError, ArithmeticError):
                add_problem(
                    rel, "envelope_invariant", "Observation v1 cross-field invariant failed"
                )

        _validate_envelope_identity(
            envelope,
            envelope_file,
            run_document,
            run_times,
            seen_capture_ids,
            duplicate_ids,
            root,
            add_problem,
        )
        referenced_assets.update(
            _validate_envelope_assets(
                envelope,
                envelope_file,
                root,
                assets_dir,
                files,
                symlinks,
                contents,
                add_problem,
            )
        )

    if assets_dir in directories:
        for asset_file in files:
            try:
                relative_asset = asset_file.relative_to(assets_dir)
            except ValueError:
                continue
            if relative_asset.parts and asset_file not in referenced_assets:
                add_problem(
                    _relative_path(root, asset_file),
                    "asset_orphan",
                    "Asset file is not referenced by an envelope",
                )

    ordered_problems = sorted(
        problems,
        key=lambda problem: (problem["path"], problem["code"], problem["message"]),
    )
    report = _report(run_id, len(envelope_files), ordered_problems)
    if problems or run_document is None:
        return report, None
    return report, ValidatedRun(
        run_document,
        [parsed_json[path] for path in envelope_files],
        {path.relative_to(assets_dir).as_posix(): contents[path] for path in referenced_assets},
    )


def _report(
    run_id: str | None, envelopes_checked: int, problems: list[dict[str, str]]
) -> dict[str, object]:
    return {
        "ok": not problems,
        "run_id": run_id,
        "envelopes_checked": envelopes_checked,
        "problems": problems,
    }


def _walk_tree(
    root: Path,
    add_problem: Any,
) -> tuple[list[Path], set[Path], set[Path], list[Path]]:
    files: list[Path] = []
    directories: set[Path] = set()
    symlinks: set[Path] = set()
    top_level: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            if directory == root:
                add_problem(".", "layout_unreadable", "Run directory entries could not be read")
            else:
                add_problem(
                    _relative_path(root, directory),
                    "layout_unreadable",
                    "Directory could not be read",
                )
            continue
        for entry in entries:
            path = Path(entry.path)
            if directory == root:
                top_level.append(path)
            if entry.name.startswith("."):
                add_problem(
                    _relative_path(root, path),
                    "layout_hidden",
                    "Names starting with a dot are not allowed",
                )
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                add_problem(
                    _relative_path(root, path),
                    "layout_entry_unreadable",
                    "Entry metadata could not be read",
                )
                continue
            is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)
            if entry.is_symlink() or is_reparse:
                symlinks.add(path)
                add_problem(
                    _relative_path(root, path),
                    "layout_symlink",
                    "Symlinks are not allowed in a run directory",
                )
                continue
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(path)
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                files.append(path)
            else:
                add_problem(
                    _relative_path(root, path),
                    "layout_entry_invalid",
                    "Run entries must be regular files or directories",
                )
    return files, directories, symlinks, top_level


def _validate_run_document(
    document: dict[str, Any] | None,
    directory_name: str,
    add_problem: Any,
) -> tuple[datetime | None, datetime | None]:
    if document is None:
        return None, None
    for field in _RUN_FIELDS:
        if field not in document:
            add_problem(
                f"run.json#/{field}", "run_required", "Required run summary field is missing"
            )

    for field in ("run_id", "grant_id"):
        value = document.get(field)
        if not _is_uuid(value):
            add_problem(f"run.json#/{field}", "run_uuid", "Field must be a UUID")
    run_id = document.get("run_id")
    if isinstance(run_id, str) and directory_name != run_id:
        add_problem("run.json#/run_id", "run_id_mismatch", "Directory name must equal run_id")

    group_key = document.get("group_key")
    if not isinstance(group_key, str) or not group_key or len(group_key) > 200:
        add_problem("run.json#/group_key", "run_type", "group_key must be a non-empty key")
    adapter_version = document.get("adapter_version")
    if not isinstance(adapter_version, str) or not adapter_version:
        add_problem(
            "run.json#/adapter_version", "run_type", "adapter_version must be a non-empty string"
        )

    for field, allowed in _RUN_ENUMS.items():
        value = document.get(field)
        if not isinstance(value, str) or value not in allowed:
            add_problem(f"run.json#/{field}", "run_enum", f"{field} has an unsupported value")

    partial_reasons = document.get("partial_reasons")
    if (
        not isinstance(partial_reasons, list)
        or any(
            not isinstance(reason, str) or reason not in _PARTIAL_REASONS
            for reason in partial_reasons
        )
        or len(partial_reasons) != len(set(partial_reasons))
    ):
        add_problem(
            "run.json#/partial_reasons",
            "run_partial_reasons",
            "partial_reasons contains an invalid value",
        )
    elif document.get("coverage") == "partial" and not partial_reasons:
        add_problem(
            "run.json#/partial_reasons", "run_partial_reasons", "Partial coverage requires a reason"
        )
    elif document.get("coverage") != "partial" and partial_reasons:
        add_problem(
            "run.json#/partial_reasons",
            "run_partial_reasons",
            "Only partial coverage may include reasons",
        )

    error_code = document.get("error_code")
    if error_code is not None and (
        not isinstance(error_code, str) or error_code not in _ERROR_CODES
    ):
        add_problem("run.json#/error_code", "run_enum", "error_code has an unsupported value")

    for field in ("cards_seen", "cards_parsed", "scroll_steps"):
        value = document.get(field)
        if type(value) is not int or value < 0:
            add_problem(f"run.json#/{field}", "run_type", f"{field} must be a non-negative integer")
    cards_seen = document.get("cards_seen")
    cards_parsed = document.get("cards_parsed")
    if type(cards_seen) is int and type(cards_parsed) is int and cards_parsed > cards_seen:
        add_problem(
            "run.json#/cards_parsed", "run_count_relation", "cards_parsed cannot exceed cards_seen"
        )

    started_at = _utc_datetime(document.get("started_at"))
    ended_at = _utc_datetime(document.get("ended_at"))
    if started_at is None:
        add_problem(
            "run.json#/started_at", "run_timestamp", "started_at must be a UTC ISO-8601 timestamp"
        )
    if ended_at is None:
        add_problem(
            "run.json#/ended_at", "run_timestamp", "ended_at must be a UTC ISO-8601 timestamp"
        )
    if started_at is not None and ended_at is not None and ended_at < started_at:
        add_problem("run.json#/ended_at", "run_time_order", "ended_at must not precede started_at")
    return started_at, ended_at


def _validate_envelope_identity(
    envelope: dict[str, Any],
    envelope_file: Path,
    run_document: dict[str, Any] | None,
    run_times: tuple[datetime | None, datetime | None],
    seen_capture_ids: set[str],
    duplicate_ids: set[str],
    root: Path,
    add_problem: Any,
) -> None:
    rel = _relative_path(root, envelope_file)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    source_kind = envelope.get("source_kind")
    if source_kind != "facebook_live":
        add_problem(
            f"{rel}#/source_kind", "envelope_source_kind", "source_kind must be facebook_live"
        )

    if run_document is not None:
        for envelope_field, run_field, code in (
            ("run_id", "run_id", "envelope_run_id_mismatch"),
            ("grant_id", "grant_id", "envelope_grant_id_mismatch"),
            ("job_kind", "job_kind", "envelope_job_kind_mismatch"),
        ):
            if envelope.get(envelope_field) != run_document.get(run_field):
                add_problem(
                    f"{rel}#/{envelope_field}", code, f"{envelope_field} does not match run.json"
                )
        if payload.get("group_key") != run_document.get("group_key"):
            add_problem(
                f"{rel}#/payload/group_key",
                "envelope_group_key_mismatch",
                "payload.group_key does not match run.json",
            )

    envelope_run_id = envelope.get("run_id")
    source_post_key = payload.get("source_post_key")
    capture_id = envelope.get("capture_id")
    expected_capture_id: str | None = None
    try:
        if (
            isinstance(envelope_run_id, str)
            and _is_uuid(envelope_run_id)
            and isinstance(source_post_key, str)
        ):
            expected_capture_id = str(uuid5(UUID(envelope_run_id), source_post_key))
    except (ValueError, TypeError):
        expected_capture_id = None
    if expected_capture_id is not None and capture_id != expected_capture_id:
        add_problem(
            f"{rel}#/capture_id",
            "capture_id_mismatch",
            "capture_id must equal uuid5(run_id, source_post_key)",
        )
    if isinstance(capture_id, str):
        if capture_id in seen_capture_ids and capture_id not in duplicate_ids:
            duplicate_ids.add(capture_id)
            add_problem(
                f"{rel}#/capture_id", "capture_id_duplicate", "capture_id is duplicated in this run"
            )
        seen_capture_ids.add(capture_id)
        if envelope_file.name != f"{capture_id}.json":
            add_problem(rel, "capture_id_filename", "Envelope filename must equal capture_id.json")

    if run_document is not None:
        started_at, ended_at = run_times
        observed_at = _utc_datetime(payload.get("observed_at"))
        if started_at is not None and observed_at is not None and observed_at < started_at:
            add_problem(
                f"{rel}#/payload/observed_at", "envelope_time_range", "observed_at precedes the run"
            )
        if ended_at is not None and observed_at is not None and observed_at > ended_at:
            add_problem(
                f"{rel}#/payload/observed_at", "envelope_time_range", "observed_at follows the run"
            )


def _validate_envelope_assets(
    envelope: dict[str, Any],
    envelope_file: Path,
    root: Path,
    assets_dir: Path,
    files: list[Path],
    symlinks: set[Path],
    contents: dict[Path, bytes],
    add_problem: Any,
) -> set[Path]:
    rel = _relative_path(root, envelope_file)
    assets = envelope.get("assets")
    if not isinstance(assets, dict):
        return set()
    referenced: set[Path] = set()
    for local_key, metadata in assets.items():
        if not isinstance(metadata, dict):
            continue
        field_path = f"{rel}#/assets/{_pointer_segment(str(local_key))}"
        staging_key = metadata.get("staging_key")
        parts = _safe_staging_parts(staging_key)
        if parts is None:
            add_problem(
                f"{field_path}/staging_key",
                "asset_path_invalid",
                "staging_key must stay inside assets",
            )
            continue
        asset_path = assets_dir.joinpath(*parts)
        parent = asset_path
        crossed_symlink = False
        while parent != assets_dir and parent != parent.parent:
            if parent in symlinks:
                crossed_symlink = True
                break
            parent = parent.parent
        if crossed_symlink:
            add_problem(
                f"{field_path}/staging_key",
                "asset_symlink",
                "Asset paths must not include symlinks",
            )
            continue
        try:
            asset_path.resolve(strict=False).relative_to(assets_dir.resolve(strict=False))
        except (OSError, ValueError):
            add_problem(
                f"{field_path}/staging_key",
                "asset_path_invalid",
                "staging_key must stay inside assets",
            )
            continue
        if asset_path not in files or asset_path not in contents:
            add_problem(
                f"{field_path}/staging_key", "asset_missing", "Referenced asset file is missing"
            )
            continue
        referenced.add(asset_path)
        data = contents[asset_path]
        if isinstance(metadata.get("sha256"), str):
            if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                add_problem(
                    f"{field_path}/sha256", "asset_sha256_mismatch", "Asset SHA-256 does not match"
                )
        if type(metadata.get("bytes")) is int and len(data) != metadata["bytes"]:
            add_problem(
                f"{field_path}/bytes", "asset_bytes_mismatch", "Asset byte count does not match"
            )
        actual_mime = _image_mime(data)
        if metadata.get("mime") != actual_mime:
            add_problem(
                f"{field_path}/mime",
                "asset_mime_mismatch",
                "Asset MIME type does not match its contents",
            )
    return referenced


def _safe_staging_parts(staging_key: object) -> tuple[str, ...] | None:
    if (
        not isinstance(staging_key, str)
        or not staging_key
        or "\\" in staging_key
        or ":" in staging_key
    ):
        return None
    posix_path = PurePosixPath(staging_key)
    windows_path = PureWindowsPath(staging_key)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return None
    parts = tuple(staging_key.split("/"))
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return None
    return parts


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _scan_json(document: Any, file_path: str, add_problem: Any) -> None:
    def visit(value: Any, parts: list[str], field_name: str | None = None) -> None:
        if isinstance(value, str):
            path = _field_path(file_path, parts)
            for pattern_name, pattern in _SECRET_PATTERNS:
                if pattern.search(value):
                    add_problem(
                        path, "secret_detected", f"Potential secret matched pattern {pattern_name}"
                    )
            if field_name in _TEXT_FIELDS and _HTML_MARKUP.search(value):
                add_problem(path, "html_markup", "HTML markup is not allowed in text fields")
        elif isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                key_path = _field_path(file_path, [*parts, key_text])
                for pattern_name, pattern in _SECRET_PATTERNS:
                    if pattern.search(key_text):
                        add_problem(
                            key_path,
                            "secret_detected",
                            f"Potential secret matched pattern {pattern_name}",
                        )
                visit(child, [*parts, key_text], key_text)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, [*parts, str(index)], field_name)

    visit(document, [])


def _scan_raw_secrets(data: bytes, path: str, add_problem: Any) -> None:
    text = data.decode("utf-8", errors="ignore").replace("\x00", "")
    for pattern_name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            add_problem(path, "secret_detected", f"Potential secret matched pattern {pattern_name}")


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or value.endswith("-00:00"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        return None
    return parsed.astimezone(UTC)


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value.lower()


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return "."


def _pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _field_path(file_path: str, parts: Sequence[object]) -> str:
    if not parts:
        return file_path
    safe_parts = [_pointer_segment(_safe_segment(str(part))) for part in parts]
    return f"{file_path}#/{'/'.join(safe_parts)}"


def _sanitize_path(path: str) -> str:
    if "#/" in path:
        file_path, pointer = path.split("#", 1)
        safe_file = "/".join(_safe_segment(part) for part in file_path.split("/"))
        safe_fields = "/".join(
            _pointer_segment(_safe_segment(part)) for part in pointer.lstrip("/").split("/")
        )
        return f"{safe_file}#/{safe_fields}" if safe_fields else safe_file
    return "/".join(_safe_segment(part) for part in path.replace("\\", "/").split("/"))


def _safe_segment(value: str) -> str:
    if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        return "<redacted>"
    return value
