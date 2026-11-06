"""Mutation tests prove the golden fixture validator detects malformed inputs."""

import json
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "fixtures/scenarios"


def validate_copy(tmp_path: Path) -> tuple[Path, object]:
    destination = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS, destination)
    sys.path.insert(0, str(ROOT / "scripts"))
    validator = import_module("check_scenarios").validate_scenarios
    return destination, validator


def edit_json(path: Path, mutate: object) -> None:
    content = json.loads(path.read_text(encoding="utf-8"))
    mutate(content)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def assert_cli_rejects(directory: Path, fragment: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_scenarios.py"),
            "--scenarios-dir",
            str(directory),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert fragment in result.stderr


def test_scenarios_are_valid(tmp_path: Path) -> None:
    directory, validate = validate_copy(tmp_path)
    assert validate(directory) == []


def test_rejects_invalid_round_schema(tmp_path: Path) -> None:
    directory, validate = validate_copy(tmp_path)
    round_file = directory / "S01-reserve-reopen/rounds/01-open.json"
    edit_json(round_file, lambda payload: payload["payload"].update(schema_version=999))
    assert_cli_rejects(directory, "observation schema")


def test_rejects_missing_arrival_round(tmp_path: Path) -> None:
    directory, validate = validate_copy(tmp_path)
    expected = directory / "S02-late-arrival/expected.json"
    edit_json(expected, lambda doc: doc["runs"][0]["arrival_order"].append("99-missing.json"))
    assert_cli_rejects(directory, "missing or invalid round")


def test_rejects_nonexistent_status_evidence(tmp_path: Path) -> None:
    directory, validate = validate_copy(tmp_path)
    expected = directory / "S03-multi-item/expected.json"
    edit_json(
        expected,
        lambda doc: doc["runs"][0]["expect"]["items"]["sku3060"]["status_evidence"].update(
            local_key="not-a-real-evidence-key"
        ),
    )
    assert_cli_rejects(directory, "status_evidence not-a-real-evidence-key")

def test_rejects_buyer_comment_as_reserved_status_evidence(tmp_path: Path) -> None:
    directory, _ = validate_copy(tmp_path)
    scenario = directory / "S03-multi-item"
    round_file = scenario / "rounds/03-seller-reserves-3060.json"
    edit_json(
        round_file,
        lambda doc: doc["payload"]["items"][0]["evidence_keys"].append("s03-buyer-text"),
    )
    expected = scenario / "expected.json"

    def use_buyer_as_seller(doc: dict) -> None:
        item = doc["runs"][1]["expect"]["items"]["sku3060"]
        item["status_history"][-1]["evidence"]["local_key"] = "s03-buyer-text"
        item["status_evidence"]["local_key"] = "s03-buyer-text"

    edit_json(expected, use_buyer_as_seller)
    assert_cli_rejects(directory, "role 'other' cannot confirm a sale status")


def test_rejects_mismatched_seller_actor_in_comment(tmp_path: Path) -> None:
    directory, _ = validate_copy(tmp_path)
    round_file = directory / "S03-multi-item/rounds/03-seller-reserves-3060.json"

    def spoof_seller(doc: dict) -> None:
        seller_comment = next(
            comment
            for comment in doc["payload"]["comments"]
            if comment["source_comment_key"] == "s03-seller"
        )
        seller_comment["actor_key"] = "different-actor"

    edit_json(round_file, spoof_seller)
    assert_cli_rejects(directory, "seller actor_key mismatch")


def test_rejects_comment_with_other_role_even_if_actor_matches(tmp_path: Path) -> None:
    directory, _ = validate_copy(tmp_path)
    round_file = directory / "S03-multi-item/rounds/03-seller-reserves-3060.json"

    def wrong_comment_role(doc: dict) -> None:
        seller_comment = next(
            comment
            for comment in doc["payload"]["comments"]
            if comment["source_comment_key"] == "s03-seller"
        )
        seller_comment["role"] = "other"

    edit_json(round_file, wrong_comment_role)
    assert_cli_rejects(directory, "not seller")


def test_accepts_source_label_as_status_evidence(tmp_path: Path) -> None:
    directory, validate = validate_copy(tmp_path)
    round_file = directory / "S01-reserve-reopen/rounds/01-open.json"

    def use_source_label(doc: dict) -> None:
        evidence = doc["payload"]["evidence"][0]
        evidence["role"] = "source_label"
        evidence["source_span"] = {
            "field": "source_label",
            "quote": doc["payload"]["items"][0]["source_status_label"],
        }

    edit_json(round_file, use_source_label)
    assert validate(directory) == []
