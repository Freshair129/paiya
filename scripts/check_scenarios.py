"""Validate synthetic A1 scenario observations and human-authored golden expectations.

No network calls and no business decision engine: expected status labels are
reviewed against the SRS, not calculated from implementation under test.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from check_contracts import problems

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "fixtures/scenarios"
OBSERVATION_SCHEMA = ROOT / "docs/architecture/contracts/observation.v1.schema.json"
SCENARIO_RE = re.compile(r"^(S\d{2})-[a-z0-9-]+$")
ROUND_RE = re.compile(r"^\d{2}-[a-z0-9-]+\.json$")


def read_json(path: Path, errors: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: cannot load JSON: {exc}")
        return None


def validate_scenarios(
    scenarios_dir: Path = SCENARIOS,
    observation_schema_path: Path = OBSERVATION_SCHEMA,
) -> list[str]:
    errors: list[str] = []
    expected_schema = read_json(scenarios_dir / "expected.schema.json", errors)
    observation_schema = read_json(observation_schema_path, errors)
    if expected_schema is None or observation_schema is None:
        return errors

    Draft202012Validator.check_schema(expected_schema)
    Draft202012Validator.check_schema(observation_schema)
    expected_validator = Draft202012Validator(expected_schema)
    observation_validator = Draft202012Validator(observation_schema)
    scenario_dirs = sorted(path for path in scenarios_dir.iterdir() if path.is_dir())
    if not scenario_dirs:
        return ["No scenarios found"]
    if len(scenario_dirs) < 4:
        errors.append(f"Expected at least S01-S04; found {len(scenario_dirs)} scenarios")

    found_ids: set[str] = set()
    for directory in scenario_dirs:
        match = SCENARIO_RE.fullmatch(directory.name)
        if not match:
            errors.append(f"{directory}: invalid scenario directory name")
            continue
        scenario_id = match.group(1)
        found_ids.add(scenario_id)
        if not (directory / "README.md").is_file():
            errors.append(f"{directory}: missing README.md")
        rounds_dir = directory / "rounds"
        if not rounds_dir.is_dir():
            errors.append(f"{directory}: missing rounds directory")
            continue

        rounds = {}
        for round_file in sorted(rounds_dir.glob("*.json")):
            if not ROUND_RE.fullmatch(round_file.name):
                errors.append(f"{round_file}: invalid round filename")
                continue
            document = read_json(round_file, errors)
            if document is None:
                continue
            failures = problems(observation_validator, document)
            errors.extend(f"{round_file}: observation schema/invariant: {p}" for p in failures)
            if not failures:
                rounds[round_file.name] = document
        if not rounds:
            errors.append(f"{directory}: no valid observation rounds")

        expected_path = directory / "expected.json"
        expected = read_json(expected_path, errors)
        if expected is None:
            continue
        violations = sorted(expected_validator.iter_errors(expected), key=lambda error: str(error.path))
        if violations:
            for violation in violations:
                errors.append(f"{expected_path}: expected schema: {violation.message}")
            continue
        if expected["scenario_id"] != scenario_id:
            errors.append(f"{expected_path}: scenario_id does not match {scenario_id}")

        for run in expected["runs"]:
            name = run["name"]
            order = run["arrival_order"]
            inputs = []
            for filename in order:
                if filename not in rounds:
                    errors.append(f"{scenario_id}/{name}: missing or invalid round {filename}")
                    continue
                inputs.append(rounds[filename])
            if len(inputs) != len(order):
                continue
            captures = {record["capture_id"] for record in inputs}
            number = run["expect"]["observations"]
            if number != len(captures):
                errors.append(
                    f"{scenario_id}/{name}: observations={number}, "
                    f"but unique capture_ids={len(captures)}"
                )
            source_items = {
                item["source_item_key"]
                for record in inputs
                for item in record["payload"].get("items", [])
                if item["source_item_key"] is not None
            }
            expected_items = run["expect"]["items"]
            if source_items != set(expected_items):
                errors.append(
                    f"{scenario_id}/{name}: expected item keys differ from round item keys"
                )
            for key, item_expect in expected_items.items():
                history = item_expect["status_history"]
                timestamps = [event["observed_at"] for event in history]
                if timestamps != sorted(timestamps):
                    errors.append(f"{scenario_id}/{name}/{key}: unordered status_history")
                if any(
                    before["status"] == after["status"]
                    for before, after in zip(history, history[1:])
                ):
                    errors.append(f"{scenario_id}/{name}/{key}: repeated adjacent status")
                if history:
                    if item_expect["current_status"] != history[-1]["status"]:
                        errors.append(f"{scenario_id}/{name}/{key}: current_status mismatch")
                    if item_expect["status_evidence"] is None:
                        errors.append(f"{scenario_id}/{name}/{key}: missing status_evidence")
                for ref in [item_expect["status_evidence"]] + [
                    event["evidence"] for event in history
                ]:
                    if ref is None:
                        continue
                    filename, local_key = ref["round"], ref["local_key"]
                    if filename not in order or filename not in rounds:
                        errors.append(f"{scenario_id}/{name}/{key}: invalid evidence round {filename}")
                        continue
                    payload = rounds[filename]["payload"]
                    evidence_keys = {e["local_key"] for e in payload.get("evidence", [])}
                    attached = {
                        evidence_key
                        for source_item in payload.get("items", [])
                        if source_item["source_item_key"] == key
                        for evidence_key in source_item.get("evidence_keys", [])
                    }
                    if local_key not in evidence_keys or local_key not in attached:
                        errors.append(
                            f"{scenario_id}/{name}/{key}: status_evidence {local_key} "
                            f"not attached to that item in {filename}"
                        )
            if "activity" in run["expect"]:
                newest = max(inputs, key=lambda record: record["payload"]["observed_at"])
                if run["expect"]["activity"] != newest["payload"].get("activity"):
                    errors.append(f"{scenario_id}/{name}: activity differs from latest round")

    for required in ("S01", "S02", "S03", "S04"):
        if required not in found_ids:
            errors.append(f"Missing required scenario {required}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate A1 synthetic scenario goldens")
    parser.add_argument("--scenarios-dir", type=Path, default=SCENARIOS)
    args = parser.parse_args()
    errors = validate_scenarios(args.scenarios_dir)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    count = len([p for p in args.scenarios_dir.iterdir() if p.is_dir()])
    print(f"Scenarios OK: {count} synthetic scenarios, all rounds and expectations valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
