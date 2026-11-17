"""Windows process handles and a kill-on-close job owned only by this service."""

import asyncio
import os
import subprocess

from paiya_collector.browser_runtime import BrowserAdapterError


class OwnedChromeProcess:
    def __init__(self, handle, job, pid):
        self.handle = handle
        self.job = job
        self.pid = pid
        self._exit_code = None

    @classmethod
    async def start(cls, args):
        if os.name != "nt":
            raise BrowserAdapterError("MCP process containment currently requires Windows")
        import win32api
        import win32con
        import win32event
        import win32job
        import win32process

        job = win32job.CreateJobObject(None, "")
        process = thread = None
        try:
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            process, thread, pid, _ = win32process.CreateProcess(
                args[0],
                subprocess.list2cmdline(args),
                None,
                None,
                False,
                win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW,
                None,
                None,
                win32process.STARTUPINFO(),
            )
            # The primary thread has never run, so no child can escape assignment.
            win32job.AssignProcessToJobObject(job, process)
            if win32process.ResumeThread(thread) != 1:
                raise BrowserAdapterError("owned Chrome primary thread did not resume normally")
            win32api.CloseHandle(thread)
            thread = None
            return cls(process, job, pid)
        except BaseException:
            # Preserve the original failure and attempt every owned-handle close,
            # even if termination or a wait itself fails. No profile PID scan.
            if process is not None:
                try:
                    win32process.TerminateProcess(process, 1)
                    win32event.WaitForSingleObject(process, 5000)
                except Exception:
                    pass
            for handle in (thread, job, process):
                if handle is not None:
                    try:
                        win32api.CloseHandle(handle)
                    except Exception:
                        pass
            raise

    @property
    def returncode(self):
        if self.handle is None:
            return self._exit_code
        import win32process

        code = win32process.GetExitCodeProcess(self.handle)
        return None if code == 259 else code

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.05)
        return self.returncode

    def terminate(self):
        import win32job

        if self.job is not None:
            win32job.TerminateJobObject(self.job, 1)

    async def release(self):
        import win32api
        import win32con
        import win32event
        import win32job

        if self.job is None:
            return
        # Retain read/wait handles to this job's descendants before termination.
        # Job accounting alone is not the process-object completion barrier.
        descendants = []
        try:
            for pid in win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectBasicProcessIdList
            ):
                try:
                    handle = win32api.OpenProcess(
                        win32con.SYNCHRONIZE | 0x1000,
                        False,
                        pid,  # QUERY_LIMITED_INFORMATION
                    )
                except Exception as exc:
                    if getattr(exc, "winerror", None) == 87:  # Process already disappeared.
                        continue
                    raise
                try:
                    belongs = win32job.IsProcessInJob(handle, self.job)
                except BaseException:
                    win32api.CloseHandle(handle)
                    raise
                if belongs:
                    descendants.append(handle)
                else:
                    win32api.CloseHandle(handle)  # PID reused outside this job: do not wait/kill.
            win32job.TerminateJobObject(self.job, 0)
            async with asyncio.timeout(5):
                while win32job.QueryInformationJobObject(
                    self.job, win32job.JobObjectBasicAccountingInformation
                )["ActiveProcesses"] or any(
                    win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_OBJECT_0
                    for handle in descendants
                ):
                    await asyncio.sleep(0.05)
        finally:
            for handle in descendants:
                win32api.CloseHandle(handle)
        self._exit_code = self.returncode
        win32api.CloseHandle(self.job)
        win32api.CloseHandle(self.handle)
        self.job = self.handle = None
