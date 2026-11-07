"""Shared evidence validation for item-level business facts."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

EvidenceSelection = tuple[Mapping[str, Any], bool, str | None, datetime | None, str]


def _evidence_eligibility(
    evidence_row: Mapping[str, Any],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
) -> tuple[bool, str | None, datetime | None, str]:
    """Apply the status contract's role, source-span, and seller checks."""
    role = cast(str, evidence_row["role"])
    source_span = cast(Mapping[str, Any] | None, evidence_row["source_span"])
    field = source_span.get("field") if source_span is not None else None

    if role == "source_label":
        return True, None, None, "unknown"
    if role == "seller" and field == "post_text":
        return True, None, None, "unknown"

    if field != "comment_text":
        return False, "no_eligible_evidence", None, "unknown"

    comment_version_id = cast(UUID | None, evidence_row["comment_version_id"])
    comment = (
        comments_by_version.get(comment_version_id) if comment_version_id is not None else None
    )
    comment_key = cast(str | None, source_span.get("comment_key")) if source_span else None
    precision = precision_by_comment.get(comment_key, "unknown") if comment_key else "unknown"
    effective_at = cast(datetime | None, comment["source_time"]) if comment is not None else None

    if comment is None:
        return False, "no_eligible_evidence", effective_at, precision
    if role != comment["role"]:
        return False, "role_mismatch", effective_at, precision
    if seller_actor_key is None:
        return False, "seller_unknown", effective_at, precision
    if comment["actor_key"] != seller_actor_key:
        return False, "actor_mismatch", effective_at, precision
    if role == "seller" and comment["role"] == "seller":
        return True, None, effective_at, precision
    return False, "no_eligible_evidence", effective_at, precision


def select_evidence_for_fact(
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
) -> EvidenceSelection | None:
    """Return the first eligible evidence, or the first available rejection."""
    first_result: EvidenceSelection | None = None
    for evidence_key in evidence_keys:
        evidence_row = evidence_by_key.get(evidence_key)
        if evidence_row is None:
            continue
        decision = _evidence_eligibility(
            evidence_row, comments_by_version, precision_by_comment, seller_actor_key
        )
        result = (evidence_row, *decision)
        if first_result is None:
            first_result = result
        if decision[0]:
            return result
    return first_result
