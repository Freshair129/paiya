"""Offline validation tests for Collector inbox run directories."""

import json
import shutil
from pathlib import Path

import pytest

from paiya.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "inbox"
VALID_RUN_ID = "11111111-2222-4333-8444-555555555555"
SECRET_CANARY = "SYNTHETIC_INBOX_CANARY_8f3d2c"


def fixture_run_dir(name: str) -> Path:
    folder_name = "wrong-directory" if name == "directory-name-mismatch" else VALID_RUN_ID
    return FIXTURES / name / folder_name


def run_validator(
    run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, object], str]:
    try:
        exit_code = main(["inbox", "validate", str(run_dir)])
    except SystemExit as error:
        exit_code = int(error.code or 0)
    output = capsys.readouterr().out
    return exit_code, json.loads(output), output


def test_valid_fixture_passes_with_report_shape(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, report, _ = run_validator(fixture_run_dir("valid-complete"), capsys)

    assert exit_code == 0
    assert report == {
        "ok": True,
        "run_id": VALID_RUN_ID,
        "envelopes_checked": 1,
        "problems": [],
    }


@pytest.mark.parametrize(
    ("fixture_name", "problem_code"),
    [
        ("missing-ready", "layout_ready_missing"),
        ("unexpected-top-level", "layout_unexpected"),
        ("hidden-entry", "layout_hidden"),
        ("directory-name-mismatch", "run_id_mismatch"),
        ("run-required-field", "run_required"),
        ("run-invalid-enum", "run_enum"),
        ("run-invalid-timestamp", "run_timestamp"),
        ("run-time-order", "run_time_order"),
        ("envelope-invalid-schema", "envelope_schema"),
        ("envelope-invalid-source-kind", "envelope_source_kind"),
        ("envelope-run-mismatch", "envelope_run_id_mismatch"),
        ("capture-id-mismatch", "capture_id_mismatch"),
        ("duplicate-capture-id", "capture_id_duplicate"),
        ("asset-missing", "asset_missing"),
        ("asset-path-traversal", "asset_path_invalid"),
        ("asset-hash-mismatch", "asset_sha256_mismatch"),
        ("asset-bytes-mismatch", "asset_bytes_mismatch"),
        ("asset-mime-mismatch", "asset_mime_mismatch"),
        ("asset-orphan", "asset_orphan"),
        ("secret-scan", "secret_detected"),
        ("secret-in-asset", "secret_detected"),
        ("html-markup", "html_markup"),
    ],
)
def test_invalid_fixture_reports_its_problem(
    fixture_name: str,
    problem_code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, report, _ = run_validator(fixture_run_dir(fixture_name), capsys)

    assert exit_code == 1
    assert report["ok"] is False
    assert problem_code in {problem["code"] for problem in report["problems"]}


def test_none_coverage_with_no_envelopes_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, report, _ = run_validator(fixture_run_dir("valid-none"), capsys)

    assert exit_code == 0
    assert report["ok"] is True
    assert report["envelopes_checked"] == 0
    assert report["problems"] == []


def test_secret_report_names_pattern_without_canary_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, report, output = run_validator(fixture_run_dir("secret-scan"), capsys)

    assert exit_code == 1
    assert "cookie_xs" in " ".join(problem["message"] for problem in report["problems"])
    if SECRET_CANARY in output:
        raise AssertionError("validator output leaked the synthetic secret canary")


def test_reports_problems_from_more_than_one_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / VALID_RUN_ID
    shutil.copytree(fixture_run_dir("valid-complete"), run_dir)
    (run_dir / "READY").unlink()
    run_file = run_dir / "run.json"
    run_document = json.loads(run_file.read_text(encoding="utf-8"))
    run_document["coverage"] = "unknown"
    run_file.write_text(json.dumps(run_document), encoding="utf-8")

    exit_code, report, _ = run_validator(run_dir, capsys)

    codes = {problem["code"] for problem in report["problems"]}
    assert exit_code == 1
    assert "layout_ready_missing" in codes
    assert "run_enum" in codes


def test_rejects_asset_symlink(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / VALID_RUN_ID
    shutil.copytree(fixture_run_dir("valid-complete"), run_dir)
    asset = run_dir / "assets" / "image-001.webp"
    asset.unlink()
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    asset.symlink_to(outside)

    exit_code, report, _ = run_validator(run_dir, capsys)

    assert exit_code == 1
    assert "asset_symlink" in {problem["code"] for problem in report["problems"]}


def test_rejects_dot_named_run_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / f".{VALID_RUN_ID}.tmp"
    shutil.copytree(fixture_run_dir("valid-complete"), run_dir)

    exit_code, report, _ = run_validator(run_dir, capsys)

    assert exit_code == 1
    assert "layout_hidden" in {problem["code"] for problem in report["problems"]}


def test_scans_cookie_header_in_all_text_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / VALID_RUN_ID
    shutil.copytree(fixture_run_dir("valid-complete"), run_dir)
    canary = "SYNTHETIC_COOKIE_HEADER_CANARY_4b71"
    (run_dir / "collector.txt").write_text(f"Cookie: opaque={canary}", encoding="utf-8")

    exit_code, report, output = run_validator(run_dir, capsys)

    messages = " ".join(problem["message"] for problem in report["problems"])
    assert exit_code == 1
    assert "cookie_header" in messages
    if canary in output:
        raise AssertionError("validator output leaked the synthetic cookie canary")


def test_untrusted_run_id_is_never_echoed_as_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / VALID_RUN_ID
    shutil.copytree(fixture_run_dir("valid-complete"), run_dir)
    run_file = run_dir / "run.json"
    run_document = json.loads(run_file.read_text(encoding="utf-8"))
    run_document["run_id"] = f"xs={SECRET_CANARY}"
    run_file.write_text(json.dumps(run_document), encoding="utf-8")

    _, _, output = run_validator(run_dir, capsys)

    if SECRET_CANARY in output:
        raise AssertionError("validator output leaked a secret from run.json")
