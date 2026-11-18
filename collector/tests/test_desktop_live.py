"""No-network checks for the desktop preflight and launcher."""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import paiya_collector.desktop_inspector as inspector
import paiya_collector.desktop_live as live
import pytest
from paiya_collector.browser_adapter import RateLimitPause
from paiya_collector.live import _check_rate_limit_pause

GROUP = "https://www.facebook.com/groups/synthetic-group"


class FakePage:
    def __init__(self, url, result):
        self._page = self
        self.main_frame = self
        self.frames = [self]
        self.url = url
        self.result = result
        self._document_revision = 7
        self.events = []

    def _guard_navigation(self):
        self.events.append("guard")

    def _check_origin(self, _url):
        self.events.append("origin")

    def _check_snapshot(self, _url, _revision):
        self.events.append("snapshot")

    def raise_if_rate_limited(self):
        self.events.append("rate_limit")

    def evaluate(self, _script, _args):
        self.events.append("evaluate")
        return self.result


def operations(tmp_path, monkeypatch, page):
    config = SimpleNamespace(group_key="owner-group-01", group_url=GROUP, selectors={})
    monkeypatch.setattr(live, "load_group_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(inspector, "load_group_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(inspector, "_check_rate_limit_pause", lambda *_args, **_kwargs: None)

    @contextmanager
    def attach(*_args, **_kwargs):
        yield page

    monkeypatch.setattr(inspector, "attach_dedicated_browser", attach)
    monkeypatch.setenv("PAIYA_COLLECTOR_INBOX", str(tmp_path / "data" / "live-inbox"))
    return live.LocalDesktopOperations(
        repository_root=tmp_path / "repo", data_root=tmp_path / "data", run_id=UUID(int=1)
    )


def test_inspection_installs_guard_and_returns_only_bounded_preview(tmp_path, monkeypatch):
    page = FakePage(
        GROUP,
        {
            "ready": True,
            "reason": "ok",
            "preview": {"post": "synthetic", "seller": "seller"},
            "source_text": "must not return",
        },
    )
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {
        "ready": True,
        "reason": "ok",
        "preview": {"post": "synthetic", "seller": "seller"},
    }
    assert page.events == ["guard", "origin", "evaluate", "snapshot", "rate_limit"]


def test_inspection_rejects_a_missing_or_empty_preview(tmp_path, monkeypatch):
    page = FakePage(GROUP, {"ready": True, "reason": "ok", "preview": {"post": "", "seller": "x"}})
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": "selectors_unverified"}


def test_challenge_url_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage("https://www.facebook.com/checkpoint/", {})
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": "challenge_required"}
    assert "evaluate" not in page.events


def test_another_group_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage("https://www.facebook.com/groups/not-selected", {})
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": "wrong_group"}
    assert "evaluate" not in page.events


def test_child_frame_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage(GROUP, {})
    page.frames.append(SimpleNamespace(url="about:blank"))
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": "embedded_frame_unverified"}
    assert "evaluate" not in page.events


@pytest.mark.parametrize(
    ("urls", "reason"),
    [
        (["chrome-error://chromewebdata/"], "embedded_frame_load_error"),
        (["https://www.facebook.com/checkpoint/"], "challenge_required"),
        (
            ["chrome-error://chromewebdata/", "https://www.facebook.com/security/check"],
            "challenge_required",
        ),
        (["https://www.facebook.com/login/"], "session_required"),
        (["https://example.invalid/checkpoint/"], "embedded_frame_unverified"),
        (["about:blank"], "embedded_frame_unverified"),
    ],
)
def test_frame_readiness_reports_the_stop_reason_without_reading_dom(
    tmp_path, monkeypatch, urls, reason
):
    page = FakePage(GROUP, {})
    page.frames.extend(SimpleNamespace(url=url) for url in urls)
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": reason}
    assert "evaluate" not in page.events


SANDBOX = "https://www.fbsbx.com/synthetic"
READY_RESULT = {
    "ready": True,
    "reason": "ok",
    "preview": {"post": "Synthetic post", "seller": "Synthetic seller"},
}


class FakeOwner:
    def __init__(self, hidden):
        self.hidden = hidden
        self.scripts = []

    def evaluate(self, script):
        self.scripts.append(script)
        return self.hidden


def child(page, url, *, hidden=True, parent="main"):
    owner = FakeOwner(hidden)
    frame = SimpleNamespace(
        url=url,
        parent_frame=page if parent == "main" else parent,
        frame_element=lambda: owner,
    )
    page.frames.append(frame)
    return frame, owner


def inspect(tmp_path, monkeypatch, page):
    ops = operations(tmp_path, monkeypatch, page)
    return inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)


def test_hidden_sandbox_frame_does_not_stop_readiness(tmp_path, monkeypatch):
    page = FakePage(GROUP, READY_RESULT)
    _frame, owner = child(page, SANDBOX)
    result = inspect(tmp_path, monkeypatch, page)
    assert result["ready"] is True
    assert page.events.count("evaluate") == 1
    # Only the owner element in the main document is evaluated, never the child document.
    assert len(owner.scripts) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.fbsbx.com/synthetic",
        "https://fbsbx.com/synthetic",
        "https://www.fbsbx.com:8443/synthetic",
        "https://www.fbsbx.com.example.invalid/synthetic",
        "https://www.fbsbx.com@example.invalid/synthetic",
        "https://example.invalid/synthetic",
        "about:blank",
        "chrome-error://chromewebdata/",
    ],
)
def test_other_child_frames_are_still_stopped_before_dom_read(tmp_path, monkeypatch, url):
    page = FakePage(GROUP, READY_RESULT)
    child(page, SANDBOX)
    child(page, url)
    result = inspect(tmp_path, monkeypatch, page)
    assert result["ready"] is False
    assert result["reason"].startswith("embedded_frame_")
    assert "evaluate" not in page.events


def test_visible_sandbox_frame_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage(GROUP, READY_RESULT)
    child(page, SANDBOX, hidden=False)
    assert inspect(tmp_path, monkeypatch, page) == {
        "ready": False,
        "reason": "embedded_frame_unverified",
    }
    assert "evaluate" not in page.events


def test_nested_sandbox_frame_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage(GROUP, READY_RESULT)
    outer, _owner = child(page, SANDBOX)
    child(page, SANDBOX, parent=outer)
    assert inspect(tmp_path, monkeypatch, page)["reason"] == "embedded_frame_unverified"
    assert "evaluate" not in page.events


def test_owner_lookup_failure_is_stopped_before_dom_read(tmp_path, monkeypatch):
    page = FakePage(GROUP, READY_RESULT)

    def fail():
        raise RuntimeError("synthetic detached frame")

    page.frames.append(SimpleNamespace(url=SANDBOX, parent_frame=page, frame_element=fail))
    assert inspect(tmp_path, monkeypatch, page)["reason"] == "embedded_frame_unverified"
    assert "evaluate" not in page.events


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://www.facebook.com/checkpoint/", "challenge_required"),
        ("https://www.facebook.com/login/", "session_required"),
        ("chrome-error://chromewebdata/", "embedded_frame_load_error"),
    ],
)
def test_sandbox_frame_does_not_hide_a_challenge_or_load_error(tmp_path, monkeypatch, url, reason):
    page = FakePage(GROUP, READY_RESULT)
    child(page, SANDBOX)
    child(page, url)
    assert inspect(tmp_path, monkeypatch, page) == {"ready": False, "reason": reason}
    assert "evaluate" not in page.events


def test_desktop_preserves_frame_load_error_for_the_gui(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    monkeypatch.setattr(
        ops,
        "_run_collector",
        lambda *_args, **_kwargs: {
            "ready": False,
            "reason": "embedded_frame_load_error",
        },
    )
    assert ops.inspect_page() == {"ready": False, "reason": "embedded_frame_load_error"}


def test_open_profile_uses_no_shell_or_source_url(tmp_path, monkeypatch):
    page = FakePage(GROUP, {})
    ops = operations(tmp_path, monkeypatch, page)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(live.subprocess, "run", run)
    ops.open_browser()
    assert GROUP not in calls[0][0]
    assert calls[0][1].get("shell", False) is False
    assert calls[0][1]["capture_output"] is True


def test_browser_process_does_not_receive_database_or_unrelated_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_DATABASE_URL", "synthetic-secret")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "synthetic-secret")
    env = live.collector_environment(Path(tmp_path))
    assert "PAIYA_DATABASE_URL" not in env
    assert "UNRELATED_API_TOKEN" not in env
    assert env["PYTHONPATH"] == str(Path(tmp_path) / "collector")


def test_collector_429_is_returned_as_a_pause_and_never_retried(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    (ops.data_root / "b2-ops").mkdir(parents=True)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=2,
            stdout='{"reason":"http_429","retry_after_until":"2026-11-18T00:00:00Z"}',
        )

    monkeypatch.setattr(live.subprocess, "run", run)
    with pytest.raises(RateLimitPause) as raised:
        ops.collect()
    assert raised.value.retry_after_until is not None
    assert len(calls) == 1
    assert ops.collect() == {"already_ingested": True}
    assert len(calls) == 1


def test_consumed_permit_can_resume_an_existing_bundle_without_source_access(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    (ops.data_root / "b2-ops").mkdir(parents=True)
    claim = ops.data_root / "b2-ops" / f"desktop-pilot-{ops.run_id}.json"
    claim.write_text("{}", encoding="utf-8")
    bundle = ops.inbox / "incoming" / str(ops.run_id)
    bundle.mkdir(parents=True)
    (bundle / "READY").write_text("", encoding="utf-8")
    (bundle / "run.json").write_text(
        '{"run_id":"' + str(ops.run_id) + '","coverage":"partial","cards_parsed":1}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops, "_run_collector", lambda *_args, **_kwargs: pytest.fail("source must not be called")
    )
    report = ops.collect()
    assert report["replayed"] is True
    assert report["coverage"] == "partial"


@pytest.mark.parametrize("deadline", [None, datetime.now(UTC) + timedelta(hours=1)])
def test_inspection_429_blocks_a_fresh_inspection(tmp_path, monkeypatch, deadline):
    page = FakePage(GROUP, {})
    ops = operations(tmp_path, monkeypatch, page)
    monkeypatch.setattr(inspector, "_check_rate_limit_pause", _check_rate_limit_pause)
    monkeypatch.setattr(
        page, "raise_if_rate_limited", lambda: (_ for _ in ()).throw(RateLimitPause(deadline))
    )
    with pytest.raises(RateLimitPause):
        inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    with pytest.raises(RateLimitPause):
        _check_rate_limit_pause(
            ops.state, "owner-group-01", now=datetime.now(UTC), clear_manual_pause=False
        )


def test_inspection_persists_429_even_on_early_wrong_group_stop(tmp_path, monkeypatch):
    page = FakePage("https://www.facebook.com/groups/not-selected", {})
    ops = operations(tmp_path, monkeypatch, page)
    monkeypatch.setattr(inspector, "_check_rate_limit_pause", _check_rate_limit_pause)
    monkeypatch.setattr(
        page, "raise_if_rate_limited", lambda: (_ for _ in ()).throw(RateLimitPause(None))
    )
    with pytest.raises(RateLimitPause):
        inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    with pytest.raises(RateLimitPause):
        _check_rate_limit_pause(
            ops.state, "owner-group-01", now=datetime.now(UTC), clear_manual_pause=False
        )


@pytest.mark.parametrize("deadline", [None, datetime(2030, 1, 1, tzinfo=UTC)])
def test_inspector_reports_pause_deadline_to_parent(tmp_path, monkeypatch, capsys, deadline):
    import sys

    monkeypatch.setattr(
        sys, "argv", ["inspector", "--repository-root", str(tmp_path), "--data-root", str(tmp_path)]
    )
    monkeypatch.setattr(
        inspector, "inspect_page", lambda **_kwargs: (_ for _ in ()).throw(RateLimitPause(deadline))
    )
    assert inspector.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == "http_429"
    assert report["retry_after_until"] == (None if deadline is None else deadline.isoformat())


def test_open_browser_never_navigates_directly_to_source(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    calls = []
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0),
    )
    ops.open_browser()
    assert GROUP not in calls[0]


def test_inbox_mismatch_is_rejected_before_collection(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    monkeypatch.setenv("PAIYA_COLLECTOR_INBOX", str(tmp_path / "different-inbox"))
    (ops.data_root / "b2-ops").mkdir(parents=True)
    monkeypatch.setattr(ops, "_run_collector", lambda *args, **kwargs: {"unexpected": "called"})
    with pytest.raises(RuntimeError, match="inbox_mismatch"):
        ops.collect()


def test_unbound_cleanup_task_is_not_accepted(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    assert not ops._retention_matches({})


def test_retention_binding_rejects_changed_runner_or_unrelated_schedule(tmp_path, monkeypatch):
    ops = operations(tmp_path, monkeypatch, FakePage(GROUP, {}))
    runner = tmp_path / "approved-retention.ps1"
    runner.write_text("synthetic approved cleanup runner", encoding="utf-8")
    binding = {
        "version": 1,
        "runner": str(runner),
        "sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "inbox": str(ops.inbox),
        "state": str(ops.state),
        "media": str(tmp_path / "media"),
        "database": "sqlite://",
    }
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", binding["media"])
    monkeypatch.setenv("PAIYA_DATABASE_URL", "sqlite://")
    path = ops.data_root / "b2-ops" / "retention-binding.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(binding), encoding="utf-8")
    task = {
        "enabled": True,
        "last_ok": True,
        "owner_matches": True,
        "daily": True,
        "action_count": 1,
        "exe": "powershell.exe",
        "args": f'-NoProfile -NonInteractive -File "{runner}" -Prune',
        "last_run": datetime.now(UTC).isoformat(),
        "next_run": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    assert ops._retention_matches(task)
    monkeypatch.setenv("PAIYA_DATABASE_URL", "sqlite:///another.db")
    assert not ops._retention_matches(task)
    monkeypatch.setenv("PAIYA_DATABASE_URL", "sqlite://")
    assert not ops._retention_matches({**task, "daily": False})
    assert not ops._retention_matches(
        {**task, "last_run": (datetime.now(UTC) - timedelta(days=10)).isoformat()}
    )
    assert not ops._retention_matches(
        {**task, "next_run": (datetime.now(UTC) + timedelta(days=10)).isoformat()}
    )
    assert not ops._retention_matches({**task, "args": '-NoProfile -File "other.ps1" -Prune'})
    import paiya_collector.state as state_module

    original_check = state_module.ensure_no_reparse_components

    def reject_changed_state(path, *, label):
        if path == ops.state:
            raise ValueError("synthetic state junction")
        return original_check(path, label=label)

    monkeypatch.setattr(state_module, "ensure_no_reparse_components", reject_changed_state)
    assert not ops._retention_matches(task)
    monkeypatch.setattr(state_module, "ensure_no_reparse_components", original_check)
    runner.write_text("changed cleanup runner", encoding="utf-8")
    assert not ops._retention_matches(task)


def test_reviewed_retention_binding_is_exclusive_and_never_contains_password(tmp_path):
    from paiya_collector.retention_binding import write_reviewed_binding

    runner = tmp_path / "runner.ps1"
    runner.write_text("synthetic runner reviewed by operator", encoding="utf-8")
    data = tmp_path / "data"
    args = dict(
        data_root=data,
        runner=runner,
        media=tmp_path / "media",
        database="postgresql://owner:synthetic-secret@localhost:5432/demo",
        confirmed=True,
    )
    write_reviewed_binding(**args)
    text = (data / "b2-ops" / "retention-binding.json").read_text(encoding="utf-8")
    assert "synthetic-secret" not in text
    assert json.loads(text)["database"] == "postgresql://owner@localhost:5432/demo"
    with pytest.raises(FileExistsError):
        write_reviewed_binding(**args)
    with pytest.raises(ValueError, match="review"):
        write_reviewed_binding(**{**args, "confirmed": False})


def test_database_identity_rejects_unverifiable_query_targets():
    from paiya_collector.retention_binding import database_identity

    for database in (
        "postgresql://owner@/demo?host=server-a",
        "postgresql://owner@/demo?host=server-b",
    ):
        with pytest.raises(ValueError, match="query"):
            database_identity(database)
    with pytest.raises(ValueError, match="explicit"):
        database_identity("postgresql://owner@/demo")
