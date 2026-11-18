"""Owner controls that launch the Collector without sharing database credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from paiya_collector.browser_adapter import RateLimitPause, load_group_config


def collector_environment(repository_root: Path) -> dict[str, str]:
    """Pass only ordinary OS paths, not Paiya credentials or arbitrary secrets."""
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "PSMODULEPATH",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(
        PYTHONPATH=str(repository_root / "collector"),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return result


class LocalDesktopOperations:
    """Orchestrate exactly the configured local group and one approved run ID."""

    def __init__(self, *, repository_root: Path, data_root: Path, run_id: UUID) -> None:
        self.repository_root = repository_root.resolve()
        self.data_root = data_root.absolute()
        self.group_config = self.data_root / "owner-group-01.json"
        self.profile = self.data_root / "browser-profiles" / "owner-group-01"
        self.inbox = self.data_root / "live-inbox"
        self.state = self.data_root / "live-state"
        self.run_id = run_id
        self.collector_python = os.environ.get("PAIYA_COLLECTOR_PYTHON", sys.executable)

    def _config(self):
        return load_group_config(self.group_config, repository_root=self.repository_root)

    def _verify_inbox(self) -> None:
        from paiya_collector.state import ensure_no_reparse_components

        configured = os.environ.get("PAIYA_COLLECTOR_INBOX")
        ensure_no_reparse_components(self.inbox, label="desktop inbox")
        if configured:
            ensure_no_reparse_components(Path(configured).absolute(), label="configured inbox")
        if not configured or Path(configured).resolve() != self.inbox.resolve():
            raise RuntimeError("inbox_mismatch")

    def _retention_matches(self, task: object) -> bool:
        from paiya_collector.retention_binding import database_identity
        from paiya_collector.state import ensure_no_reparse_components

        try:
            binding_path = self.data_root / "b2-ops" / "retention-binding.json"
            ensure_no_reparse_components(binding_path, label="retention binding")
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            if (
                not isinstance(task, dict)
                or not isinstance(binding, dict)
                or binding.get("version") != 1
            ):
                return False
            if not all(
                task.get(key) is True for key in ("enabled", "last_ok", "owner_matches", "daily")
            ):
                return False
            if task.get("action_count") != 1:
                return False
            now = datetime.now(UTC)
            last = datetime.fromisoformat(str(task["last_run"]).replace("Z", "+00:00"))
            next_run = datetime.fromisoformat(str(task["next_run"]).replace("Z", "+00:00"))
            if last.tzinfo is None or next_run.tzinfo is None:
                return False
            if not (
                now - timedelta(hours=48) <= last <= now < next_run <= now + timedelta(hours=48)
            ):
                return False
            if Path(str(task.get("exe", ""))).name.lower() not in ("powershell.exe", "pwsh.exe"):
                return False
            args = re.fullmatch(
                r'-NoProfile -NonInteractive -File "([^"]+)" -Prune',
                str(task.get("args", "")),
                re.IGNORECASE,
            )
            if args is None:
                return False
            runner = Path(binding["runner"]).absolute()
            task_runner = Path(args.group(1)).absolute()
            ensure_no_reparse_components(runner, label="retention runner")
            ensure_no_reparse_components(task_runner, label="scheduled retention runner")
            if task_runner != runner:
                return False
            if hashlib.sha256(runner.read_bytes()).hexdigest() != binding.get("sha256"):
                return False
            if database_identity(os.environ["PAIYA_DATABASE_URL"]) != binding.get("database"):
                return False
            media = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
            if not media:
                return False
            for key, target in (
                ("inbox", self.inbox),
                ("state", self.state),
                ("media", Path(media)),
            ):
                reviewed = Path(binding[key]).absolute()
                current = target.absolute()
                ensure_no_reparse_components(reviewed, label="reviewed retention path")
                ensure_no_reparse_components(current, label="current retention path")
                if reviewed != current:
                    return False
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def verify_grant(self) -> None:
        from sqlalchemy import create_engine

        from paiya.grants.collector import verify_collector_grant

        self._verify_inbox()
        config = self._config()
        if os.name != "nt":
            raise RuntimeError("retention_task_unavailable")
        task_check = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$t=Get-ScheduledTask -TaskName 'PaiyaCollectorRetention-owner-group-01' "
                "-ErrorAction Stop; $i=$t | Get-ScheduledTaskInfo; "
                "$sid=([Security.Principal.NTAccount]$t.Principal.UserId).Translate("
                "[Security.Principal.SecurityIdentifier]).Value; "
                "$daily=@($t.Triggers | Where-Object { $_.Enabled -and "
                "$_.DaysInterval -eq 1 -and -not $_.EndBoundary }).Count -gt 0; "
                "@{enabled=[bool]$t.Settings.Enabled; last_ok=($i.LastTaskResult -eq 0); "
                "owner_matches=($sid -eq "
                "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value); "
                "daily=$daily; action_count=@($t.Actions).Count; "
                "last_run=$i.LastRunTime.ToUniversalTime().ToString('o'); "
                "next_run=$i.NextRunTime.ToUniversalTime().ToString('o'); "
                "exe=[string]$t.Actions[0].Execute; "
                "args=[string]$t.Actions[0].Arguments} | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=collector_environment(self.repository_root),
        )
        try:
            task = json.loads(task_check.stdout)
        except (TypeError, ValueError):
            task = None
        if task_check.returncode or not self._retention_matches(task):
            raise RuntimeError("retention_task_unavailable")
        engine = create_engine(os.environ["PAIYA_DATABASE_URL"])
        try:
            with engine.connect() as connection:
                verify_collector_grant(
                    connection, UUID(config.grant_id), config.group_key, datetime.now(UTC)
                )
        finally:
            engine.dispose()

    def _run_collector(self, module: str, arguments: list[str], *, timeout: int) -> dict:
        result = subprocess.run(
            [self.collector_python, "-B", "-m", module, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
            env=collector_environment(self.repository_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError("collector_operation_failed") from None
        if not isinstance(value, dict):
            raise RuntimeError("collector_result_invalid")
        if result.returncode:
            if value.get("reason") == "http_429":
                raw = value.get("retry_after_until")
                deadline = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
                raise RateLimitPause(deadline)
            raise RuntimeError("collector_operation_failed")
        return value

    def inspect_page(self) -> dict[str, object]:
        result = self._run_collector(
            "paiya_collector.desktop_inspector",
            ["--repository-root", str(self.repository_root), "--data-root", str(self.data_root)],
            timeout=30,
        )
        reasons = {
            "ok",
            "challenge_required",
            "session_required",
            "wrong_group",
            "no_visible_posts",
            "selectors_unverified",
            "security_scan_incomplete",
            "inspection_unavailable",
            "rate_limited",
            "embedded_frame_unverified",
            "embedded_frame_load_error",
        }
        reason = result.get("reason")
        if (
            not isinstance(result.get("ready"), bool)
            or reason not in reasons
            or (result["ready"] and reason != "ok")
        ):
            return {"ready": False, "reason": "inspection_unavailable"}
        if not result["ready"]:
            return {"ready": False, "reason": reason}
        preview = result.get("preview")
        if not isinstance(preview, dict):
            return {"ready": False, "reason": "inspection_unavailable"}
        post, seller = preview.get("post"), preview.get("seller")
        if (
            not isinstance(post, str)
            or not post.strip()
            or not isinstance(seller, str)
            or not seller.strip()
        ):
            return {"ready": False, "reason": "inspection_unavailable"}
        return {
            "ready": True,
            "reason": "ok",
            "preview": {"post": post[:300], "seller": seller[:100]},
        }

    def open_browser(self) -> None:
        script = self.repository_root / "collector" / "scripts" / "open-dedicated-chrome.ps1"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "RemoteSigned",
                "-File",
                str(script),
                "-ProfileDir",
                str(self.profile),
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
            env=collector_environment(self.repository_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError("dedicated_browser_not_opened")

    def collect(self) -> dict[str, object]:
        from paiya_collector.state import ensure_no_reparse_components

        self._verify_inbox()
        receipt = self.inbox / "done" / str(self.run_id) / "result.json"
        claim = self.data_root / "b2-ops" / f"desktop-pilot-{self.run_id}.json"
        ensure_no_reparse_components(claim, label="desktop pilot claim")
        if receipt.is_file():
            return {"already_ingested": True}
        try:
            with claim.open("x", encoding="utf-8") as marker:
                json.dump({"version": 1, "run_id": str(self.run_id)}, marker)
                marker.flush()
                os.fsync(marker.fileno())
        except FileExistsError:
            # A consumed permit may only resume import of its already-published
            # bundle. It can never open the source a second time, even after
            # the Collector's ordinary seven-day state retention expires.
            return self._claimed_report()
        return self._run_collector(
            "paiya_collector.cli",
            [
                "discover-browser",
                "--group-config",
                str(self.group_config),
                "--profile-dir",
                str(self.profile),
                "--inbox",
                str(self.inbox),
                "--state-dir",
                str(self.state),
                "--run-id",
                str(self.run_id),
                "--confirm-live",
            ],
            timeout=240,
        )

    def pending(self) -> dict[str, object] | None:
        from paiya_collector.state import ensure_no_reparse_components

        claim = self.data_root / "b2-ops" / f"desktop-pilot-{self.run_id}.json"
        ensure_no_reparse_components(claim, label="desktop pilot claim")
        return self._claimed_report() if claim.is_file() else None

    def _claimed_report(self) -> dict[str, object]:
        from paiya_collector.state import ensure_no_reparse_components

        bundle = self.inbox / "incoming" / str(self.run_id)
        ensure_no_reparse_components(bundle, label="desktop pilot bundle")
        ensure_no_reparse_components(bundle / "READY", label="desktop pilot marker")
        ensure_no_reparse_components(bundle / "run.json", label="desktop pilot record")
        if not (bundle / "READY").is_file():
            return {"already_ingested": True}
        try:
            run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"already_ingested": True}
        if not isinstance(run, dict) or run.get("run_id") != str(self.run_id):
            return {"already_ingested": True}
        return {
            "replayed": True,
            "run_id": str(self.run_id),
            "bundle_path": str(bundle),
            "coverage": run.get("coverage"),
            "error_code": run.get("error_code"),
            "cards_parsed": run.get("cards_parsed", 0),
        }

    def validate(self, path: str) -> bool:
        from paiya.inbox.validation import validate_inbox

        return bool(validate_inbox(Path(path)).get("ok"))

    def ingest(self, run_id: UUID) -> dict[str, object]:
        from sqlalchemy import create_engine

        from paiya.inbox.service import ingest_inbox_run

        self._verify_inbox()
        engine = create_engine(os.environ["PAIYA_DATABASE_URL"])
        try:
            return ingest_inbox_run(engine, run_id)
        finally:
            engine.dispose()
