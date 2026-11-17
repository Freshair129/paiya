"""No-network checks for the desktop preflight and launcher."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import paiya_collector.desktop_inspector as inspector
import paiya_collector.desktop_live as live
import pytest
from paiya_collector.browser_adapter import RateLimitPause

GROUP = "https://www.facebook.com/groups/synthetic-group"


class FakePage:
    def __init__(self, url, result):
        self._page = self
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
    return live.LocalDesktopOperations(
        repository_root=tmp_path / "repo", data_root=tmp_path / "data", run_id=UUID(int=1)
    )


def test_inspection_installs_guard_and_returns_only_presence_flags(tmp_path, monkeypatch):
    page = FakePage(GROUP, {"ready": True, "reason": "ok", "source_text": "must not return"})
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": True, "reason": "ok"}
    assert page.events == ["guard", "origin", "evaluate", "snapshot", "rate_limit"]


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
    page.frames.append(object())
    ops = operations(tmp_path, monkeypatch, page)
    result = inspector.inspect_page(repository_root=ops.repository_root, data_root=ops.data_root)
    assert result == {"ready": False, "reason": "embedded_frame_unverified"}
    assert "evaluate" not in page.events


def test_open_group_uses_approved_local_config_without_shell(tmp_path, monkeypatch):
    page = FakePage(GROUP, {})
    ops = operations(tmp_path, monkeypatch, page)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(live.subprocess, "run", run)
    ops.open_browser()
    assert calls[0][0][-2:] == ["-InitialUrl", GROUP]
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
