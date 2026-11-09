"""Run the offline O3 release smoke checks against the configured database."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else type(error).__name__


def _alembic_check(engine: Engine) -> dict[str, Any]:
    try:
        config = Config(str(ROOT / "alembic.ini"))
        expected = sorted(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            current = sorted(MigrationContext.configure(connection).get_current_heads())
        return {
            "name": "alembic_head",
            "ok": bool(expected) and current == expected,
            "current": current,
            "expected": expected,
        }
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as error:
        return {"name": "alembic_head", "ok": False, "code": _error_code(error)}


def _purge_log_check(engine: Engine) -> dict[str, Any]:
    from paiya.purge.errors import PurgeError
    from paiya.purge.startup import check_purge_log

    try:
        check_purge_log(engine)
        return {"name": "purge_log", "ok": True}
    except PurgeError as error:
        return {"name": "purge_log", "ok": False, "code": error.code}
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as error:
        return {"name": "purge_log", "ok": False, "code": _error_code(error)}


def _health_cli_check() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "paiya.cli", "health"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"name": "paiya_health_json", "ok": False, "code": _error_code(error)}

    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "name": "paiya_health_json",
            "ok": False,
            "code": "invalid_json",
            "exit_code": completed.returncode,
        }

    is_health_report = isinstance(report, dict) and isinstance(report.get("checked_at"), str)
    return {
        "name": "paiya_health_json",
        "ok": completed.returncode == 0 and is_health_report,
        "exit_code": completed.returncode,
        "json": is_health_report,
    }


def _read_api_sample(engine: Engine) -> dict[str, Any]:
    from paiya.api.app import app
    from paiya.auth.sessions import SESSION_COOKIE_NAME, create_session, revoke_session
    from paiya.read.context import read_context_group_key_from_env

    result: dict[str, Any] = {"name": "read_api_sample", "ok": False}
    session = None
    try:
        group_key = read_context_group_key_from_env()
        if group_key is None:
            result["code"] = "read_context_required"
        else:
            with engine.begin() as connection:
                session = create_session(connection)
            port = os.environ.get("PAIYA_PORT", "8000")
            with TestClient(app, base_url=f"http://127.0.0.1:{port}") as client:
                client.cookies.set(SESSION_COOKIE_NAME, session.token)
                response = client.get("/v1/groups")
            body = response.json() if response.status_code == 200 else {}
            groups = body.get("groups") if isinstance(body, dict) else None
            matched = isinstance(groups, list) and any(
                isinstance(group, dict) and group.get("group_key") == group_key
                for group in groups
            )
            result = {
                "name": "read_api_sample",
                "ok": response.status_code == 200 and matched,
                "group_key": group_key,
                "group_count": len(groups) if isinstance(groups, list) else 0,
                "http_status": response.status_code,
            }
    except Exception as error:
        result = {"name": "read_api_sample", "ok": False, "code": _error_code(error)}
    finally:
        if session is not None:
            with engine.begin() as connection:
                revoke_session(connection, session.session_id)
            result["session_revoked"] = True

    return result


def _testclient_check() -> dict[str, Any]:
    from paiya.api.app import app

    try:
        port = os.environ.get("PAIYA_PORT", "8000")
        with TestClient(app, base_url=f"http://127.0.0.1:{port}") as client:
            health_response = client.get("/healthz")
        return {
            "name": "testclient_healthz",
            "ok": health_response.status_code == 200,
            "get_healthz": health_response.status_code,
        }
    except Exception as error:
        return {"name": "testclient_healthz", "ok": False, "code": _error_code(error)}


def run_smoke_checks() -> dict[str, Any]:
    from paiya.settings import get_database_engine

    checks: list[dict[str, Any]] = []
    try:
        engine = get_database_engine()
        checks.append(_alembic_check(engine))
        checks.append(_purge_log_check(engine))
        checks.append(_health_cli_check())
        checks.append(_read_api_sample(engine))
        checks.append(_testclient_check())
        engine.dispose()
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as error:
        checks.append({"name": "database_configuration", "ok": False, "code": _error_code(error)})

    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def main() -> int:
    report = run_smoke_checks()
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
