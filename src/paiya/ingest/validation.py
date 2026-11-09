"""Validate fixture observation v1 envelopes before any persistence writes."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

SCHEMA_V1 = (
    Path(__file__).resolve().parents[3] / "docs/architecture/contracts/observation.v1.schema.json"
)
SCHEMA_V2 = (
    Path(__file__).resolve().parents[3] / "docs/architecture/contracts/observation.v2.schema.json"
)
# Preserve the previous import name for callers that inspect the v1 contract.
SCHEMA = SCHEMA_V1


def validate_observation(document: dict[str, Any]) -> None:
    """Validate an observation by payload schema_version without altering v1 rules."""
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Invalid observation: payload must be an object")
    version = payload.get("schema_version")
    if version == 1:
        _validate_v1_observation(document)
        return
    if version == 2:
        _validate_v2_observation(document)
        return
    raise ValueError(f"Unsupported observation schema_version: {version!r}")


def _validate_v1_observation(document: dict[str, Any]) -> None:
    """Enforce the unchanged v1 JSON Schema and cross-field invariants."""
    schema = json.loads(SCHEMA_V1.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(document))
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise ValueError(f"Invalid observation v1 ({path}): {first.message}")

    payload = document["payload"]
    evidence = {entry["local_key"]: entry for entry in payload.get("evidence", [])}
    if len(evidence) != len(payload.get("evidence", [])):
        raise ValueError("Duplicate evidence local_key")
    comments = payload.get("comments", [])
    comment_keys = [
        entry["source_comment_key"] for entry in comments if entry["source_comment_key"]
    ]
    if len(comment_keys) != len(set(comment_keys)):
        raise ValueError("Duplicate source_comment_key")

    activity = payload.get("activity")
    if activity is not None:
        if activity["distinct_known_commenters_seen"] > activity["distinct_comments_seen"]:
            raise ValueError("Known commenter count exceeds seen comments")
        if comments and len(comments) != activity["distinct_comments_seen"]:
            raise ValueError("Visible comment count differs from comments array")

    for entry in payload.get("items", []):
        if any(key not in evidence for key in entry.get("evidence_keys", [])):
            raise ValueError("Item refers to missing evidence local_key")
        price = entry.get("price")
        if price is not None and "amount_max" in price:
            if Decimal(price["amount_max"]) < Decimal(price["amount"]):
                raise ValueError("price amount_max is below amount")
    if any(key not in evidence for key in document["assets"]):
        raise ValueError("Asset refers to missing evidence")

    texts = {
        entry["source_comment_key"]: entry["text"]
        for entry in comments
        if entry["source_comment_key"] is not None
    }
    for key, entry in evidence.items():
        if entry["media_state"] == "captured" and key not in document["assets"]:
            raise ValueError(f"Captured evidence {key} has no asset")
        span = entry.get("source_span")
        if not span or span["field"] != "comment_text":
            continue
        text = texts.get(span.get("comment_key"))
        if text is None:
            raise ValueError(f"Evidence {key} refers to missing comment")
        if "start" in span or "end" in span:
            start, end = span.get("start", 0), span.get("end", len(text))
            if start > end or text[start:end] != span["quote"]:
                raise ValueError(f"Evidence {key} comment offsets do not match quote")
        elif span["quote"] not in text:
            raise ValueError(f"Evidence {key} quote not found in comment")


def _validate_v2_observation(document: dict[str, Any]) -> None:
    """Enforce manual-share v2 structure, evidence scope and quote integrity."""
    schema = json.loads(SCHEMA_V2.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(document))
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise ValueError(f"Invalid observation v2 ({path}): {first.message}")

    payload = document["payload"]
    if payload["seller"] is not None:
        raise ValueError("Manual-share author must remain unknown")
    evidence_entries = payload["evidence"]
    evidence = {entry["local_key"]: entry for entry in evidence_entries}
    if len(evidence) != len(evidence_entries):
        raise ValueError("Duplicate evidence local_key")

    def validate_fact(fact: dict[str, Any], scope_key: str, assigned: set[str] | None) -> None:
        evidence_key = fact["evidence_key"]
        if evidence_key not in evidence:
            raise ValueError(f"Price fact refers to missing evidence local_key: {evidence_key}")
        if assigned is not None and evidence_key not in assigned:
            raise ValueError(f"Price fact evidence is outside item scope: {evidence_key}")
        if assigned is None and evidence_key in item_evidence_keys:
            raise ValueError(f"Unassigned price fact uses item evidence: {evidence_key}")
        amount_max = fact.get("amount_max")
        if amount_max is not None and Decimal(amount_max) < Decimal(fact["amount"]):
            raise ValueError("price amount_max is below amount")
        fact_identity = (scope_key, fact["kind"], fact["unit"], evidence_key)
        if fact_identity in fact_identities:
            raise ValueError("Duplicate price fact key")
        fact_identities.add(fact_identity)

    item_evidence_keys: set[str] = set()
    fact_identities: set[tuple[str, str, str, str]] = set()
    for item in payload["items"]:
        item_keys = set(item["evidence_keys"])
        item_evidence_keys.update(item_keys)
        if any(key not in evidence for key in item_keys):
            missing = next(key for key in item_keys if key not in evidence)
            raise ValueError(f"Item refers to missing evidence local_key: {missing}")
        item_key = item["source_item_key"]
        for fact in item["price_facts"]:
            validate_fact(fact, item_key, item_keys)
    for fact in payload["unassigned_price_facts"]:
        validate_fact(fact, "post", None)

    text = payload["post_text"]
    for key, entry in evidence.items():
        span = entry["source_span"]
        if span["field"] != "post_text":
            raise ValueError(f"Manual evidence {key} must refer to post_text")
        start = span.get("start")
        end = span.get("end")
        if (start is None) != (end is None):
            raise ValueError(f"Evidence {key} must provide both quote offsets or neither")
        if start is not None:
            if start > end or text[start:end] != span["quote"]:
                raise ValueError(f"Evidence {key} post_text offsets do not match quote")
        elif span["quote"] not in text:
            raise ValueError(f"Evidence {key} quote not found in post_text")
        if entry["media_state"] == "captured" and key not in document["assets"]:
            raise ValueError(f"Captured evidence {key} has no asset")
    if any(key not in evidence for key in document["assets"]):
        raise ValueError("Asset refers to missing evidence local_key")
