import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from paiya_collector.browser_adapter import RateLimitPause
from paiya_collector.mcp_policy import ProfileLease, RequestPolicy


def test_redirect_is_denied_before_request_and_failure_latches():
    policy = RequestPolicy("http://localhost:1234/groups/demo", synthetic=True)
    assert policy.allow("http://localhost:1234/groups/demo", document=True)
    assert not policy.allow("http://localhost:4321/target", document=True)
    with pytest.raises(RuntimeError, match="guard"):
        policy.check()


def test_subresource_credentials_never_leave_origin():
    policy = RequestPolicy("http://localhost:1234/groups/demo", synthetic=True)
    assert not policy.allow("http://localhost:4321/image", document=False)
    assert not policy.allow("http://user@localhost:1234/image", document=False)


def test_429_uses_receipt_time_and_invalid_retry_after_requires_manual_resume():
    policy = RequestPolicy("http://localhost:1234/groups/demo", synthetic=True)
    received = datetime(2026, 11, 17, tzinfo=UTC)
    policy.response(429, "120", received)
    with pytest.raises(RateLimitPause) as caught:
        policy.check()
    assert caught.value.retry_after_until == received + timedelta(seconds=120)
    policy.response(429, None, received)
    policy.response(429, "30", received)
    with pytest.raises(RateLimitPause) as caught:
        policy.check()
    assert caught.value.manual_resume_required


def test_synthetic_policy_cannot_accept_real_hosts():
    with pytest.raises(ValueError, match="loopback"):
        RequestPolicy("https://www.facebook.com/groups/synthetic", synthetic=True)


def profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile"
    path.mkdir()
    (path / "PAIYA_COLLECTOR_PROFILE").write_text("owner-group-01")
    return path


def test_profile_lease_excludes_concurrent_owner_and_preserves_files(tmp_path):
    path = profile(tmp_path)
    (path / "sentinel").write_text("preserve")
    with ProfileLease(path, repository_root=tmp_path / "repo"):
        with pytest.raises(ValueError, match="owned|locked"):
            with ProfileLease(path, repository_root=tmp_path / "repo"):
                pytest.fail("second owner acquired the profile")
    assert (path / "sentinel").read_text() == "preserve"
    with ProfileLease(path, repository_root=tmp_path / "repo"):
        pass


def test_active_port_marker_is_never_removed(tmp_path):
    path = profile(tmp_path)
    marker = path / "DevToolsActivePort"
    marker.write_text("1234\n/devtools/browser/synthetic")
    with pytest.raises(ValueError, match="DevTools"):
        with ProfileLease(path, repository_root=tmp_path / "repo"):
            pytest.fail("active profile accepted")
    assert marker.read_text() == "1234\n/devtools/browser/synthetic"


def test_unmarked_profile_is_rejected(tmp_path):
    path = tmp_path / "profile"
    path.mkdir()
    with pytest.raises(ValueError, match="marker"):
        with ProfileLease(path, repository_root=tmp_path / "repo"):
            pytest.fail("unowned profile accepted")


@pytest.mark.parametrize("state", ["exited", "gone", "running", "inaccessible"])
def test_matching_process_metadata_requires_liveness_proof(tmp_path, monkeypatch, state):
    import psutil

    path = profile(tmp_path)

    class BrowserSnapshot:
        info = {"name": "chrome.exe"}
        pid = 123

        def cmdline(self):
            # Process enumeration/metadata may outlive termination. Matching
            # arguments alone must not assert that this process is still live.
            return ["chrome.exe", f"--user-data-dir={path}"]

        def wait(self, timeout):
            pytest.fail("cached process_iter exit status must not authorize profile reuse")

    class FreshProcess:
        def wait(self, timeout):
            assert timeout == 0
            if state == "gone":
                raise psutil.NoSuchProcess(123)
            if state == "running":
                raise psutil.TimeoutExpired(timeout, pid=123)
            if state == "inaccessible":
                raise psutil.AccessDenied(123)
            return 0

    monkeypatch.setattr(psutil, "process_iter", lambda *args: iter([BrowserSnapshot()]))
    monkeypatch.setattr(psutil, "Process", lambda pid: FreshProcess())
    if state in {"exited", "gone"}:
        with ProfileLease(path, repository_root=tmp_path / "repo"):
            pass
    else:
        with pytest.raises(ValueError, match="running browser|cannot be verified"):
            with ProfileLease(path, repository_root=tmp_path / "repo"):
                pytest.fail("live or unverifiable profile owner was accepted")
    assert (path / "PAIYA_COLLECTOR_PROFILE").read_text() == "owner-group-01"


def test_mcp_default_profile_boundary_includes_repository_siblings(tmp_path, monkeypatch):
    from paiya_collector import mcp_browser

    repo = tmp_path / "repo"
    path = repo / "other" / "profile"
    path.mkdir(parents=True)
    (path / "PAIYA_COLLECTOR_PROFILE").write_text("owner-group-01")
    (path / "PAIYA_SYNTHETIC_PROFILE").write_text("synthetic-only")
    monkeypatch.setattr(
        mcp_browser, "__file__", str(repo / "collector/paiya_collector/mcp_browser.py")
    )
    monkeypatch.setitem(sys.modules, "nodriver", SimpleNamespace())
    with pytest.raises(ValueError, match="outside"):
        asyncio.run(
            mcp_browser.GuardedBrowser().open(
                path, group_url="http://localhost:1234/groups/demo", selectors={}, synthetic=True
            )
        )
    assert not (path / "PAIYA_MCP_LOCK").exists()


def test_live_default_config_boundary_is_repository_root(tmp_path, monkeypatch):
    from paiya_collector import live

    repo = tmp_path / "repo"
    monkeypatch.setattr(live, "__file__", str(repo / "collector/paiya_collector/live.py"))

    def check_boundary(path, *, repository_root, synthetic):
        assert repository_root == repo
        raise ValueError("verified repository root before IO")

    monkeypatch.setattr(live, "load_group_config", check_boundary)
    with pytest.raises(ValueError, match="verified repository"):
        live.discover_browser(
            tmp_path / "config.json", profile_dir=tmp_path, inbox_root=tmp_path, state_dir=tmp_path
        )


def test_close_retries_job_release_after_root_exit_and_preserves_lease_until_success():
    from paiya_collector.mcp_browser import GuardedBrowser

    released = []
    exits = []

    class Process:
        returncode = 0

        async def release(self):
            released.append(True)
            if len(released) == 1:
                raise TimeoutError("owned descendant has not exited")

    class Lease:
        def __exit__(self, *args):
            exits.append(True)

    async def run():
        browser = GuardedBrowser()
        browser.process = Process()
        browser.lease = Lease()
        with pytest.raises(TimeoutError):
            await browser.close()
        assert not exits and not browser._released
        assert await browser.close() == {"closed": True}
        assert exits == [True] and len(released) == 2
        await browser.close()
        assert exits == [True] and len(released) == 2

    asyncio.run(run())
