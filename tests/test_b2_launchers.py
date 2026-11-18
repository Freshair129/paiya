"""Synthetic checks that the B2 entry points resolve maintained source from one checkout.

The launchers are Windows PowerShell scripts for the owner's B2 machine. These tests
run them against an empty temporary data root; no database, browser or source is used.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
B2 = ROOT / "scripts" / "b2"
ENV_SCRIPT = B2 / "Paiya-B2.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="B2 launchers are Windows PowerShell scripts"
)


def run_script(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "b2-data"
    root.mkdir()
    return root


def test_environment_resolves_source_from_this_checkout_and_data_from_the_external_root(
    data_root: Path,
) -> None:
    result = run_script(ENV_SCRIPT, "-DataRoot", str(data_root), "-ValidateOnly")
    assert result.returncode == 0, result.stderr
    settings = json.loads(result.stdout)

    for key in ("repository", "python", "collector_source", "paiya_source"):
        assert Path(settings[key]).is_relative_to(ROOT), key
    assert Path(settings["collector_source"]) == ROOT / "collector"
    assert Path(settings["paiya_source"]) == ROOT / "src"
    for key in ("data_root", "inbox", "state", "media", "purge_log"):
        assert Path(settings[key]).is_relative_to(data_root), key
        assert not Path(settings[key]).is_relative_to(ROOT), key
    assert settings["database"] == "postgresql+psycopg://paiya_b2@127.0.0.1:55434/paiya_b2"


@pytest.mark.parametrize("inside", ["", "collector", "web\\dist"])
def test_data_root_inside_the_repository_is_rejected(inside: str) -> None:
    result = run_script(ENV_SCRIPT, "-DataRoot", str(ROOT / inside), "-ValidateOnly")
    assert result.returncode != 0
    assert "outside the repository" in result.stderr
    assert result.stdout.strip() == ""


def test_repository_inside_the_data_root_is_rejected() -> None:
    result = run_script(ENV_SCRIPT, "-DataRoot", str(ROOT.parent), "-ValidateOnly")
    assert result.returncode != 0
    assert "outside the repository" in result.stderr


def test_missing_data_root_is_rejected(tmp_path: Path) -> None:
    result = run_script(ENV_SCRIPT, "-DataRoot", str(tmp_path / "absent"), "-ValidateOnly")
    assert result.returncode != 0
    assert "does not exist" in result.stderr


def test_cleanup_stops_without_the_database_credential(data_root: Path) -> None:
    for name in ("live-inbox", "live-state"):
        (data_root / name).mkdir()
    result = run_script(ENV_SCRIPT, "-DataRoot", str(data_root), "-Prune")
    assert result.returncode != 0
    assert "database credential is not available" in result.stderr
    assert "postgresql" not in result.stdout + result.stderr


def test_launchers_do_not_reference_source_outside_the_checkout() -> None:
    for script in sorted(B2.glob("*.ps1")):
        text = script.read_text(encoding="ascii")
        assert "paiya-wt" not in text.lower(), script.name
        assert "$PSScriptRoot" in text, script.name
