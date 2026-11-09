"""Validate v1 and v2 contract fixtures and doc examples against JSON Schemas.

Valid fixtures must pass the schema and cross-field rules below. Invalid
fixtures must fail at least one check. Requires ``jsonschema``.
"""

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs/architecture/contracts"
SCHEMA = CONTRACTS / "observation.v1.schema.json"
SCHEMA_V2 = CONTRACTS / "observation.v2.schema.json"
DOC = CONTRACTS / "collector-and-query.md"
FIXTURES = ROOT / "fixtures/contract/observation"
FIXTURES_V2 = FIXTURES / "v2"


def rule_errors(envelope):
    """Unchanged v1 invariants that JSON Schema cannot express."""
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
        if (
            price
            and "amount_max" in price
            and Decimal(price["amount_max"]) < Decimal(price["amount"])
        ):
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
            if text[span.get("start", 0) : span.get("end", len(text))] != span["quote"]:
                errors.append(f"evidence {key} offsets do not match quote")
        elif span["quote"] not in text:
            errors.append(f"evidence {key} quote not found in comment")
    return errors


def problems(validator, document):
    found = [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in validator.iter_errors(document)
    ]
    return found or rule_errors(document)


def v2_rule_errors(envelope):
    """Manual-share evidence and fact invariants for observation v2."""
    errors = []
    payload = envelope.get("payload", {})
    if payload.get("seller") is not None:
        errors.append("manual-share seller identity must remain unknown")
    evidence_entries = payload.get("evidence", [])
    evidence = {entry["local_key"]: entry for entry in evidence_entries}
    if len(evidence) != len(evidence_entries):
        errors.append("duplicate evidence local_key")
    post_text = payload.get("post_text", "")
    item_evidence_keys = set()
    identities = set()
    for item in payload.get("items", []):
        item_keys = set(item.get("evidence_keys", []))
        item_evidence_keys.update(item_keys)
        for key in item_keys:
            if key not in evidence:
                errors.append(f"item evidence_key {key} has no evidence entry")
        for fact in item.get("price_facts", []):
            key = fact.get("evidence_key")
            if key not in evidence:
                errors.append(f"item price fact evidence_key {key} has no evidence entry")
            elif key not in item_keys:
                errors.append(f"item price fact evidence_key {key} is outside item scope")
            if "amount_max" in fact and Decimal(fact["amount_max"]) < Decimal(fact["amount"]):
                errors.append("amount_max is below amount")
            identity = (item.get("source_item_key"), fact.get("kind"), fact.get("unit"), key)
            if identity in identities:
                errors.append("duplicate price fact key")
            identities.add(identity)
    for fact in payload.get("unassigned_price_facts", []):
        key = fact.get("evidence_key")
        if key not in evidence:
            errors.append(f"unassigned price fact evidence_key {key} has no evidence entry")
        elif key in item_evidence_keys:
            errors.append(f"unassigned price fact evidence_key {key} is assigned to an item")
        if "amount_max" in fact and Decimal(fact["amount_max"]) < Decimal(fact["amount"]):
            errors.append("amount_max is below amount")
        identity = ("post", fact.get("kind"), fact.get("unit"), key)
        if identity in identities:
            errors.append("duplicate price fact key")
        identities.add(identity)
    for key, entry in evidence.items():
        if entry.get("role") != "unknown":
            errors.append(f"manual evidence {key} must keep role unknown")
        span = entry.get("source_span")
        if not span:
            continue
        if span.get("field") != "post_text":
            errors.append(f"manual evidence {key} must refer to post_text")
        start, end = span.get("start"), span.get("end")
        if (start is None) != (end is None):
            errors.append(f"evidence {key} must provide both quote offsets or neither")
        elif start is not None:
            if start > end or post_text[start:end] != span["quote"]:
                errors.append(f"evidence {key} offsets do not match quote")
        elif span.get("quote") not in post_text:
            errors.append(f"evidence {key} quote not found in post_text")
        if entry.get("media_state") == "captured" and key not in envelope.get("assets", {}):
            errors.append(f"evidence {key} is captured but has no asset")
    for key in envelope.get("assets", {}):
        if key not in evidence:
            errors.append(f"asset {key} has no evidence entry")
    return errors


def v2_problems(validator, document):
    found = [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in validator.iter_errors(document)
    ]
    return found or v2_rule_errors(document)


def main():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    envelope = Draft202012Validator(schema)
    payload = Draft202012Validator({**schema, "$ref": "#/$defs/payload"})
    schema_v2 = json.loads(SCHEMA_V2.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema_v2)
    envelope_v2 = Draft202012Validator(schema_v2, format_checker=FormatChecker())
    payload_v2 = Draft202012Validator(
        {**schema_v2, "$ref": "#/$defs/payload"}, format_checker=FormatChecker()
    )
    failures, checked = [], 0

    for file in sorted((FIXTURES / "valid").glob("*.json")):
        checked += 1
        found = problems(envelope, json.loads(file.read_text(encoding="utf-8")))
        failures.extend(f"{file.relative_to(ROOT)}: {problem}" for problem in found)
    for file in sorted((FIXTURES / "invalid").glob("*.json")):
        checked += 1
        if not problems(envelope, json.loads(file.read_text(encoding="utf-8"))):
            failures.append(f"{file.relative_to(ROOT)}: expected a violation but passed")

    for file in sorted((FIXTURES_V2 / "valid").glob("*.json")):
        checked += 1
        found = v2_problems(envelope_v2, json.loads(file.read_text(encoding="utf-8")))
        failures.extend(f"{file.relative_to(ROOT)}: {problem}" for problem in found)
    for file in sorted((FIXTURES_V2 / "invalid").glob("*.json")):
        checked += 1
        if not v2_problems(envelope_v2, json.loads(file.read_text(encoding="utf-8"))):
            failures.append(f"{file.relative_to(ROOT)}: expected a v2 violation but passed")

    blocks = re.findall(r"```json\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    if not blocks:
        failures.append(f"{DOC.relative_to(ROOT)}: no json example found")
    for number, block in enumerate(blocks, 1):
        checked += 1
        example = json.loads(block)
        version = example.get("schema_version", 1)
        validator = payload_v2 if version == 2 else payload
        found = [
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in validator.iter_errors(example)
        ]
        if version == 2:
            found.extend(v2_rule_errors({"payload": example, "assets": {}}))
        failures.extend(
            f"{DOC.relative_to(ROOT)} json example {number}: {problem}" for problem in found
        )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(
        f"Contracts OK: {checked} fixtures and examples checked against "
        f"{SCHEMA.name} and {SCHEMA_V2.name}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
