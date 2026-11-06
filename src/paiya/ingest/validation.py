"""Validate fixture observation v1 envelopes before any persistence writes."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

SCHEMA = (
    Path(__file__).resolve().parents[3] / "docs/architecture/contracts/observation.v1.schema.json"
)


def validate_observation(document: dict[str, Any]) -> None:
    """Enforce the v1 JSON Schema and cross-field reference invariants."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
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
        if "comments" in payload and len(comments) != activity["distinct_comments_seen"]:
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
