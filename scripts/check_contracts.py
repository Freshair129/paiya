"""Validate contract fixtures and doc examples against the JSON Schemas.

Valid fixtures must pass the schema and the cross-field rules below.
Invalid fixtures must fail at least one of them. Requires `jsonschema`.
"""

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs/architecture/contracts"
SCHEMA = CONTRACTS / "observation.v1.schema.json"
DOC = CONTRACTS / "collector-and-query.md"
FIXTURES = ROOT / "fixtures/contract/observation"


def rule_errors(envelope):
    """Invariants that JSON Schema cannot express. See collector-and-query.md."""
    errors = []
    payload = envelope.get("payload", {})
    evidence = {e["local_key"]: e for e in payload.get("evidence", [])}
    comments = payload.get("comments", [])
    comment_keys = [c["source_comment_key"] for c in comments if c["source_comment_key"]]
    if len(comment_keys) != len(set(comment_keys)):
        errors.append("duplicate source_comment_key")
    activity = payload.get("activity")
    if activity:
        if activity["distinct_known_commenters_seen"] > activity["distinct_comments_seen"]:
            errors.append("distinct_known_commenters_seen exceeds distinct_comments_seen")
        if comments and len(comments) != activity["distinct_comments_seen"]:
            errors.append("distinct_comments_seen differs from comments sent")
    for item in payload.get("items", []):
        for key in item.get("evidence_keys", []):
            if key not in evidence:
                errors.append(f"item evidence_key {key} has no evidence entry")
        price = item.get("price")
        if price and "amount_max" in price and Decimal(price["amount_max"]) < Decimal(price["amount"]):
            errors.append("amount_max is below amount")
    for key in envelope.get("assets", {}):
        if key not in evidence:
            errors.append(f"asset {key} has no evidence entry")
    texts = {c["source_comment_key"]: c["text"] for c in comments if c["source_comment_key"]}
    for key, item in evidence.items():
        if item["media_state"] == "captured" and key not in envelope.get("assets", {}):
            errors.append(f"evidence {key} is captured but has no asset")
        span = item.get("source_span")
        if not span or span["field"] != "comment_text":
            continue
        text = texts.get(span.get("comment_key"))
        if text is None:
            errors.append(f"evidence {key} points to an unknown comment")
        elif "start" in span or "end" in span:
            if text[span.get("start", 0):span.get("end", len(text))] != span["quote"]:
                errors.append(f"evidence {key} offsets do not match quote")
        elif span["quote"] not in text:
            errors.append(f"evidence {key} quote not found in comment")
    return errors


def problems(validator, document):
    found = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
             for e in validator.iter_errors(document)]
    return found or rule_errors(document)


def main():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    envelope = Draft202012Validator(schema)
    payload = Draft202012Validator({**schema, "$ref": "#/$defs/payload"})
    failures, checked = [], 0

    for file in sorted((FIXTURES / "valid").glob("*.json")):
        checked += 1
        found = problems(envelope, json.loads(file.read_text(encoding="utf-8")))
        failures.extend(f"{file.relative_to(ROOT)}: {p}" for p in found)
    for file in sorted((FIXTURES / "invalid").glob("*.json")):
        checked += 1
        if not problems(envelope, json.loads(file.read_text(encoding="utf-8"))):
            failures.append(f"{file.relative_to(ROOT)}: expected a violation but passed")

    blocks = re.findall(r"```json\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    if not blocks:
        failures.append(f"{DOC.relative_to(ROOT)}: no json example found")
    for n, block in enumerate(blocks, 1):
        checked += 1
        found = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                 for e in payload.iter_errors(json.loads(block))]
        failures.extend(f"{DOC.relative_to(ROOT)} json example {n}: {p}" for p in found)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Contracts OK: {checked} fixtures and examples checked against {SCHEMA.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
