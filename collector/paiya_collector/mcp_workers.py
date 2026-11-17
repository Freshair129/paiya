"""Paused worker sessions with request guards, including persisted restarts.

The extra CDP socket is local to the exact owned browser. It reads protocol
metadata only; no cookies, credentials or response bodies are requested.
"""

import asyncio
import json
from datetime import UTC, datetime

from paiya_collector.browser_runtime import BrowserAdapterError, _http_origin


class WorkerGuard:
    def __init__(self, owner):
        self.owner = owner
        self.socket = None
        self.listener = None
        self.commands = {}
        self.jobs = set()
        self.sessions = {}
        self.guarded = set()
        self.counter = 0
        self.closed = False
        self.attachments = 0

    async def send(self, method, params=None, *, session=None):
        if self.closed or self.socket is None or self.socket.close_code is not None:
            raise BrowserAdapterError("worker guard transport is unavailable")
        if len(self.commands) >= 64:
            self.owner._trip()
            raise BrowserAdapterError("worker guard command bound exceeded")
        self.counter += 1
        command_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.commands[command_id] = future
        message = {"id": command_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        try:
            await self.socket.send(json.dumps(message))
            result = await asyncio.wait_for(future, 10)
            return result
        finally:
            self.commands.pop(command_id, None)

    def schedule(self, coro):
        if self.closed:
            coro.close()
            return
        if len(self.jobs) + len(self.owner.pending) >= 64:
            coro.close()
            self.owner._trip()
            return
        task = asyncio.create_task(coro)
        self.jobs.add(task)

        def done(result):
            self.jobs.discard(result)
            if not result.cancelled() and result.exception() and not self.closed:
                self.owner._trip()

        task.add_done_callback(done)

    async def _listen(self):
        import nodriver as uc

        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self.commands.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(BrowserAdapterError("worker CDP command failed"))
                        else:
                            future.set_result(message.get("result", {}))
                    continue
                method = message.get("method")
                event = message.get("params", {})
                session = message.get("sessionId")
                if method == "Target.attachedToTarget":
                    self.schedule(self._attach(event))
                elif method == "Target.detachedFromTarget":
                    detached = event["sessionId"]
                    self.sessions.pop(detached, None)
                    self.guarded.discard(detached)
                elif method == "Inspector.targetCrashed":
                    self.guarded.discard(session)
                elif method == "Inspector.targetReloadedAfterCrash":
                    self.schedule(self._restart(session))
                elif method == "Network.responseReceived":
                    response = event.get("response", {})
                    retry = next(
                        (
                            value
                            for key, value in response.get("headers", {}).items()
                            if key.casefold() == "retry-after"
                        ),
                        None,
                    )
                    self.owner.policy.response(response.get("status"), retry, datetime.now(UTC))
                elif method == "Fetch.requestPaused":
                    if session is not None and session not in self.guarded:
                        self.owner._trip()
                        continue
                    parsed = uc.cdp.fetch.RequestPaused.from_json(event)

                    async def handle(parsed=parsed, session=session):
                        async def send(method, params=None):
                            return await self.send(method, params, session=session)

                        await self.owner._handle_paused(parsed, send=send)

                    self.schedule(handle())
        finally:
            for future in tuple(self.commands.values()):
                if not future.done():
                    future.set_exception(BrowserAdapterError("worker guard transport was lost"))
            if not self.closed:
                self.owner._trip()

    async def _auto_attach(self, session=None):
        filters = [{"type": "service_worker", "exclude": True}] if session else []
        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": True,
                "flatten": True,
                "filter": filters
                + [
                    {"type": "browser_ui", "exclude": True},
                    {"type": "tab", "exclude": True},
                    {"type": "browser", "exclude": True},
                    {},
                ],
            },
            session=session,
        )

    async def _attach(self, event):
        session = event["sessionId"]
        info = event["targetInfo"]
        self.attachments += 1
        if self.attachments > 32:
            raise BrowserAdapterError("worker target bound exceeded")
        self.sessions[session] = info
        main = info["targetId"] == str(self.owner.tab.target.target_id)
        if main:
            await self._auto_attach(session)
            await self.send("Runtime.runIfWaitingForDebugger", session=session)
            return
        if info["type"] not in {"worker", "shared_worker", "service_worker"}:
            raise BrowserAdapterError("unexpected page or opaque frame target")
        url = info.get("url", "")
        if url.startswith(("chrome-extension:", "chrome:")):
            if not event.get("waitingForDebugger"):
                await self.send("Target.closeTarget", {"targetId": info["targetId"]})
            return
        origin_url = url[5:] if url.startswith("blob:") else url
        if _http_origin(origin_url) != self.owner.policy.origin:
            raise BrowserAdapterError("worker origin is outside the source")
        if self.owner.gate.armed and not event.get("waitingForDebugger"):
            raise BrowserAdapterError("worker started before request guard")
        await self._configure(session)

    async def _restart(self, session):
        info = self.sessions.get(session)
        if info is None or info["type"] != "service_worker":
            raise BrowserAdapterError("unexpected target restart")
        if info.get("url", "").startswith(("chrome-extension:", "chrome:")):
            # Built-in workers stay paused. Repeated close causes Chrome to
            # restart them; neither execution nor a restart loop is useful here.
            return
        self.attachments += 1
        if self.attachments > 32:
            raise BrowserAdapterError("worker target bound exceeded")
        # Chrome keeps the same target/session for a stopped service worker.
        # Its Inspector reload notification, not a new attachment, is the
        # pause-before-script boundary. Reinstall interception before resuming.
        await self._configure(session)

    async def _configure(self, session):
        await self._auto_attach(session)
        await self.send("Network.enable", session=session)
        await self.send("Network.setBypassServiceWorker", {"bypass": True}, session=session)
        await self.send("Network.setCacheDisabled", {"cacheDisabled": True}, session=session)
        # Mark before Fetch.enable's response: an already-active startup worker
        # can emit a paused request while the startup proxy is still closed.
        self.guarded.add(session)
        if self.sessions[session]["type"] == "service_worker":
            await self.send(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "*", "requestStage": "Request"},
                        {"urlPattern": "*", "requestStage": "Response"},
                    ]
                },
                session=session,
            )
        # Dedicated/shared targets do not implement Fetch. Their requests,
        # including initial scripts, are intercepted by the already-enabled
        # browser-level Fetch guard on this same fail-closed socket.
        await self.send("Runtime.runIfWaitingForDebugger", session=session)

    async def start(self):
        from websockets.asyncio.client import connect

        self.socket = await connect(
            self.owner.browser.connection.websocket_url, max_size=2 * 1024 * 1024, max_queue=64
        )
        self.listener = asyncio.create_task(self._listen())
        # Worker entry/update scripts are fetched by Chrome before the worker
        # target exists. Browser-level interception covers that loader as well
        # as all other owned-browser requests; per-worker interception remains
        # installed before worker execution and on every persistent restart.
        await self.send(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "*", "requestStage": "Request"},
                    {"urlPattern": "*", "requestStage": "Response"},
                ]
            },
        )
        await self._auto_attach()
        await self.barrier()

    async def barrier(self):
        await self.send("Target.getTargets")
        while self.jobs:
            await asyncio.wait_for(asyncio.gather(*tuple(self.jobs)), 12)
        self.owner.policy.check()

    async def close(self):
        self.closed = True
        for task in tuple(self.jobs):
            task.cancel()
        if self.socket is not None:
            await self.socket.close()
        if self.listener is not None:
            await asyncio.gather(self.listener, return_exceptions=True)
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
