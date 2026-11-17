import asyncio
import sys
from types import SimpleNamespace

import pytest
from paiya_collector import mcp_process
from paiya_collector.mcp_process import OwnedChromeProcess


@pytest.mark.parametrize("failure", ["job_setup", "terminate", "wait", "close"])
def test_start_failure_preserves_error_and_attempts_all_owned_handle_closes(monkeypatch, failure):
    monkeypatch.setattr(mcp_process, "os", SimpleNamespace(name="nt"))
    closed = []
    original = RuntimeError("original assignment/setup failure")

    def fail_original(*args):
        raise original

    def fail_cleanup(*args):
        raise OSError("cleanup failed")

    def close(handle):
        closed.append(handle)
        if failure == "close" and handle == "thread":
            fail_cleanup()

    def create(*args):
        assert args[4] is False  # The private job handle cannot be inherited.
        assert args[5] & 4  # CREATE_SUSPENDED before assignment.
        return "process", "thread", 123, 456

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(CloseHandle=close))
    monkeypatch.setitem(
        sys.modules, "win32con", SimpleNamespace(CREATE_SUSPENDED=4, CREATE_NO_WINDOW=8)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32event",
        SimpleNamespace(WaitForSingleObject=fail_cleanup if failure == "wait" else lambda *a: 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32job",
        SimpleNamespace(
            CreateJobObject=lambda *a: "job",
            QueryInformationJobObject=lambda *a: {"BasicLimitInformation": {"LimitFlags": 0}},
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192,
            SetInformationJobObject=fail_original if failure == "job_setup" else lambda *a: None,
            AssignProcessToJobObject=fail_original,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(
            CreateProcess=create,
            STARTUPINFO=lambda: None,
            TerminateProcess=fail_cleanup if failure == "terminate" else lambda *a: None,
            ResumeThread=lambda *a: pytest.fail("failed assignment must never resume the child"),
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(OwnedChromeProcess.start(["synthetic-chrome"]))
    assert caught.value is original
    assert closed == (["job"] if failure == "job_setup" else ["thread", "job", "process"])


def test_release_waits_for_owned_process_signal_and_ignores_reused_foreign_pid(monkeypatch):
    closed = []
    waits = []

    def wait(handle, timeout):
        assert handle == "owned" and timeout == 0
        waits.append(handle)
        return 258 if len(waits) == 1 else 0

    def close(handle):
        if handle == "job":
            assert len(waits) >= 2, "job released before owned descendant became signaled"
        closed.append(handle)

    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(OpenProcess=lambda rights, inherit, pid: pid, CloseHandle=close),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(SYNCHRONIZE=0x100000))
    monkeypatch.setitem(
        sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=wait, WAIT_OBJECT_0=0)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32job",
        SimpleNamespace(
            JobObjectBasicProcessIdList=1,
            JobObjectBasicAccountingInformation=2,
            QueryInformationJobObject=lambda job, kind: (
                ("owned", "foreign") if kind == 1 else {"ActiveProcesses": 0}
            ),
            IsProcessInJob=lambda handle, job: handle == "owned",
            TerminateJobObject=lambda job, code: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "win32process", SimpleNamespace(GetExitCodeProcess=lambda handle: 0)
    )
    process = OwnedChromeProcess("root", "job", 123)
    asyncio.run(process.release())
    assert closed == ["foreign", "owned", "job", "root"]
    assert process.job is None and process.handle is None
