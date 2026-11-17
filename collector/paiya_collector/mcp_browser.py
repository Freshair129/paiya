"""Owned Chrome process and guarded CDP operations, without upstream MCP tools.

Only stealth_browser_mcp.cdp_raw.raw_command is reused. The upstream server,
browser manager, launcher, response capture and stealth hooks are never imported.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from paiya_collector.browser_adapter import classify_page
from paiya_collector.browser_runtime import _READ_BODY, BrowserAdapterError
from paiya_collector.mcp_frames import READ_FRAMES, READ_SAFE_SNAPSHOT
from paiya_collector.mcp_gate import StartupGate
from paiya_collector.mcp_policy import ProfileLease, RequestPolicy
from paiya_collector.mcp_process import OwnedChromeProcess

MAX_SNAPSHOT_ENCODED_BYTES = 16 * 1024 * 1024
MAX_PENDING_EVENTS = 64


class GuardedBrowser:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.process = None
        self.browser = None
        self.tab = None
        self.lease = None
        self.gate = None
        self.asset_slots = asyncio.Semaphore(2)
        self.policy = None
        self.pending = set()
        self.revision = 0
        self.frame_id = None
        self.selectors = {}
        self._closed = False
        self._released = False
        self._close_lock = asyncio.Lock()
        self._transport_waiters = []
        self._transport_sockets = {}
        self._shutdown_task = None
        self._tripped = False
        self._bootstrap = False
        self.worker_guard = None
        self._reading_paused = False

    def _trip(self):
        if self._closed or self._tripped:
            return
        self._tripped = True
        self.policy.failed = True
        # Revoke forwarding synchronously before scheduling any await/cleanup.
        if self.gate is not None:
            self.gate.armed = False
            for writer in tuple(self.gate.writers):
                writer.close()
        if self.process is not None:
            try:
                self.process.terminate()
            except Exception:
                pass
        self._shutdown_task = asyncio.create_task(self.close())
        self._shutdown_task.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )

    def _watch_transports(self):
        def lost(_):
            self._trip()

        for connection in (self.tab, self.browser.connection):
            socket = connection.websocket
            listener = connection._listener_task
            if (
                socket is None
                or socket.close_code is not None
                or listener is None
                or listener.done()
            ):
                raise BrowserAdapterError("MCP transport was lost before arming")
            self._transport_sockets[id(connection)] = socket
            listener.add_done_callback(lost)
            waiter = asyncio.create_task(socket.wait_closed())
            waiter.add_done_callback(lost)
            self._transport_waiters.append(waiter)

    async def _cdp(self, method, params=None, *, browser=False):
        from stealth_browser_mcp.cdp_raw import raw_command

        if self._closed and method != "Browser.close":
            raise BrowserAdapterError("MCP browser session is closed")
        connection = self.browser.connection if browser else self.tab
        expected = self._transport_sockets.get(id(connection))
        if (
            not self._closed
            and expected is not None
            and (
                connection.websocket is not expected
                or expected.close_code is not None
                or connection._listener_task.done()
            )
        ):
            self._trip()
            raise BrowserAdapterError("MCP transport cannot reconnect without its guard")
        try:
            return await asyncio.wait_for(connection.send(raw_command(method, params)), 10)
        except Exception as exc:
            if self.policy is not None and not self._closed:
                self.policy.failed = True
            raise BrowserAdapterError("MCP CDP operation failed") from exc

    async def open(
        self, profile: Path, *, group_url: str, selectors: dict, synthetic: bool, asset_origins=()
    ):
        import nodriver as uc

        if self.process is not None or self._closed:
            raise BrowserAdapterError("MCP session can own only one browser")
        self.policy = RequestPolicy(group_url, synthetic=synthetic, asset_origins=asset_origins)
        self.selectors = selectors
        if synthetic:
            marker = profile / "PAIYA_SYNTHETIC_PROFILE"
            if (
                not marker.is_file()
                or marker.read_text(encoding="utf-8").strip() != "synthetic-only"
            ):
                raise ValueError("synthetic profile marker is required")
        self.lease = ProfileLease(profile, repository_root=Path(__file__).resolve().parents[2])
        self.lease.__enter__()
        try:
            self.gate = StartupGate(self.policy)
            await self.gate.start()
            executable = uc.Config(user_data_dir=str(profile)).browser_executable_path
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            args = [
                executable,
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={port}",
                "--enable-automation",
                "--remote-debugging-address=127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--disable-extensions",
                "--disable-default-apps",
                "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                f"--proxy-server=http://localhost:{self.gate.port}",
                "--proxy-bypass-list=<-loopback>",
                "about:blank",
            ]
            if synthetic:
                args.insert(-1, "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost")
            self.process = await OwnedChromeProcess.start(args)
            listening = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.process.returncode is not None:
                    raise BrowserAdapterError("owned Chrome exited before DevTools was ready")
                try:
                    import psutil

                    connections = await asyncio.to_thread(
                        psutil.Process(self.process.pid).net_connections, kind="tcp"
                    )
                    listening = any(
                        connection.status == psutil.CONN_LISTEN
                        and connection.laddr
                        and connection.laddr.port == port
                        and connection.laddr.ip == "127.0.0.1"
                        for connection in connections
                    )
                    if listening:
                        break
                except (psutil.Error, OSError):
                    pass
                await asyncio.sleep(0.05)
            if not listening:
                raise BrowserAdapterError("owned Chrome DevTools startup timed out")
            config = uc.Config(user_data_dir=str(profile), host="127.0.0.1", port=port)
            config.autodiscover_targets = False
            self.browser = uc.Browser(config)
            await asyncio.wait_for(self.browser.start(), 10)
            # We own the subprocess handle; never let global nodriver cleanup own it.
            from nodriver.core import util

            util.get_registered_instances().discard(self.browser)
            tabs = self.browser.tabs
            if len(tabs) != 1 or tabs[0].target.url != "about:blank":
                raise BrowserAdapterError("profile restored unexpected tabs; refusing capture")
            self.tab = tabs[0]
            await self._install_guard()
            from paiya_collector.mcp_workers import WorkerGuard

            self.worker_guard = WorkerGuard(self)
            await self.worker_guard.start()
            await self._admit_profile()
            # Bootstrap must bypass persisted handlers so it remains inert.
            # Normal navigation may use them only after every worker session
            # has its interception installed and restart auto-attachment armed.
            await self._cdp("Network.setBypassServiceWorker", {"bypass": False})
            self._watch_transports()
            self.policy.check()
            self.gate.armed = True
            return {"opened": True, "pid": self.process.pid, "synthetic": synthetic}
        except BaseException:
            await self.close()
            raise

    async def _install_guard(self):
        import nodriver as uc

        def schedule(coro):
            if self._closed:
                coro.close()
                return
            worker_jobs = len(self.worker_guard.jobs) if self.worker_guard else 0
            if len(self.pending) + worker_jobs >= MAX_PENDING_EVENTS:
                coro.close()
                self._trip()
                return
            task = asyncio.create_task(coro)
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)

        async def paused(event, *, send=None):
            send = send or self._cdp
            try:
                if event.response_error_reason is not None:
                    # A failed response is a terminal response-stage event, not
                    # another request to authorize or relay. Never reissue it.
                    if event.resource_type == uc.cdp.network.ResourceType.DOCUMENT:
                        self.policy.failed = True
                    await send(
                        "Fetch.failRequest",
                        {
                            "requestId": str(event.request_id),
                            "errorReason": event.response_error_reason.value,
                        },
                    )
                    return
                if event.response_status_code is not None:
                    headers = event.response_headers or []
                    retry = next(
                        (
                            entry.value
                            for entry in headers
                            if entry.name.casefold() == "retry-after"
                        ),
                        None,
                    )
                    self.policy.response(event.response_status_code, retry, datetime.now(UTC))
                    if self.policy.limited or self.policy.failed:
                        await send(
                            "Fetch.failRequest",
                            {"requestId": str(event.request_id), "errorReason": "BlockedByClient"},
                        )
                        return
                    params = {"requestId": str(event.request_id)}
                    await send("Fetch.continueResponse", params)
                    return
                document = event.resource_type == uc.cdp.network.ResourceType.DOCUMENT
                if (
                    self._bootstrap
                    and document
                    and str(event.frame_id) == self.frame_id
                    and event.request.url == self.policy.group_url
                ):
                    await send(
                        "Fetch.fulfillRequest",
                        {
                            "requestId": str(event.request_id),
                            "responseCode": 200,
                            "responseHeaders": [
                                {"name": "Content-Type", "value": "text/html"},
                                {"name": "Cache-Control", "value": "no-store"},
                            ],
                            "body": base64.b64encode(
                                b"<!doctype html><title>Paiya</title>"
                                b'<link rel="icon" href="data:,">'
                            ).decode("ascii"),
                        },
                    )
                    return
                allowed = self.policy.allow(
                    event.request.url,
                    document=document,
                    resource_type=event.resource_type.value,
                    method=event.request.method,
                    has_body=bool(event.request.has_post_data),
                )
                method = "Fetch.continueRequest" if allowed else "Fetch.failRequest"
                params = {"requestId": str(event.request_id)}
                if not allowed:
                    params["errorReason"] = "BlockedByClient"
                elif self.policy.is_external_asset(event.request.url):
                    # Never continue a browser request to an external asset: CDP
                    # header replacement does not prevent cookie store mutation.
                    from paiya_collector.mcp_assets import fetch_asset

                    async with self.asset_slots:
                        asset = await fetch_asset(
                            event.request.url,
                            policy=self.policy,
                            resource_type=event.resource_type.value,
                        )
                    await send(
                        "Fetch.fulfillRequest", {"requestId": str(event.request_id), **asset}
                    )
                    return
                await send(method, params)
            except asyncio.CancelledError:
                self.policy.failed = True
                raise
            except Exception:
                self.policy.failed = True
                try:
                    await send(
                        "Fetch.failRequest",
                        {"requestId": str(event.request_id), "errorReason": "BlockedByClient"},
                    )
                except BaseException:
                    pass  # Request stays paused; operation timeout closes owned Chrome.

        def navigated(event):
            self.revision += 1

        def history(event):
            if str(event.frame_id) == self.frame_id:
                self.revision += 1

        def frame_attached(_):
            self.revision += 1

        def response_received(event):
            # A service worker can synthesize/cache a response without a Fetch
            # response-stage pause. Observe status/Retry-After only, never body.
            retry = next(
                (
                    value
                    for key, value in event.response.headers.items()
                    if key.casefold() == "retry-after"
                ),
                None,
            )
            self.policy.response(event.response.status, retry, datetime.now(UTC))

        def destroyed(event):
            if str(event.target_id) == str(self.tab.target.target_id) and not self._closed:
                self.policy.failed = True

        self._handle_paused = paused

        self.tab.add_handler(uc.cdp.fetch.RequestPaused, lambda e: schedule(paused(e)))
        self.tab.add_handler(uc.cdp.page.FrameNavigated, navigated)
        self.tab.add_handler(uc.cdp.page.NavigatedWithinDocument, history)
        self.tab.add_handler(uc.cdp.page.FrameAttached, frame_attached)
        self.tab.add_handler(uc.cdp.page.FrameDetached, frame_attached)
        self.tab.add_handler(uc.cdp.network.ResponseReceived, response_received)
        self.browser.connection.add_handler(uc.cdp.target.TargetDestroyed, destroyed)
        await self._cdp("Target.setDiscoverTargets", {"discover": True}, browser=True)
        await self._cdp("Page.enable")
        tree = await self._cdp("Page.getFrameTree")
        self.frame_id = tree["frameTree"]["frame"]["id"]
        await self._cdp("Network.enable")
        await self._cdp("Network.setBypassServiceWorker", {"bypass": True})
        await self._cdp("Network.setCacheDisabled", {"cacheDisabled": True})
        await self._cdp(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "*", "requestStage": "Request"},
                    {"urlPattern": "*", "requestStage": "Response"},
                ]
            },
        )

    async def _admit_profile(self):
        import nodriver as uc

        # Establish only the origin, with an inert response supplied by Fetch.
        # The network gate stays closed; this does not read the source or cookies.
        fired = asyncio.Event()
        self.tab.add_handler(uc.cdp.page.LoadEventFired, lambda _: fired.set())
        self._bootstrap = True
        try:
            await self._cdp("Page.navigate", {"url": self.policy.group_url})
            await asyncio.wait_for(fired.wait(), 10)
            result = await self._cdp(
                "Runtime.evaluate",
                {
                    "expression": "navigator.serviceWorker.getRegistrations().then(r=>r.length)",
                    "awaitPromise": True,
                    "returnByValue": True,
                    "timeout": 8000,
                },
            )
            count = result.get("result", {}).get("value")
            if result.get("exceptionDetails") or type(count) is not int:
                raise BrowserAdapterError("MCP service-worker registration check was incomplete")
            await self.worker_guard.barrier()
        finally:
            self._bootstrap = False
            self.tab.remove_handler(uc.cdp.page.LoadEventFired)

    async def _check(self):
        if self.policy is None or self.process is None or self.process.returncode is not None:
            raise BrowserAdapterError("MCP owned browser is unavailable")
        if self.pending:
            await asyncio.wait_for(asyncio.gather(*tuple(self.pending)), 12)
        self.policy.check()
        if self.gate is None or self.gate.server is None or not self.gate.server.is_serving():
            self.policy.failed = True
            self.policy.check()
        if self.tab not in self.browser.tabs:
            self.policy.failed = True
            self.policy.check()

    async def _evaluate(self, expression: str, *, context=None, expected_revision=None):
        if expected_revision is not None and self.revision != expected_revision:
            raise BrowserAdapterError("MCP frame tree changed before isolated card scan")
        params = {"expression": expression, "returnByValue": True}
        if context is not None:
            params["contextId"] = context
        result = await self._cdp("Runtime.evaluate", params)
        if result.get("exceptionDetails"):
            raise BrowserAdapterError("MCP page evaluation failed")
        return result.get("result", {}).get("value")

    async def _world(self):
        result = await self._cdp(
            "Page.createIsolatedWorld",
            {"frameId": self.frame_id, "worldName": "paiya-collector-isolated"},
        )
        return result["executionContextId"]

    async def navigate(self, url: str):
        await self._check()
        if not self.policy.allow(url, document=True):
            self.policy.check()
        import nodriver as uc

        fired = asyncio.Event()

        def loaded(_):
            fired.set()

        self.tab.add_handler(uc.cdp.page.LoadEventFired, loaded)
        try:
            result = await self._cdp("Page.navigate", {"url": url})
            await self._check()
            if result.get("errorText"):
                self.navigation_error = result["errorText"]
                raise BrowserAdapterError("MCP guarded navigation was blocked")
            try:
                await asyncio.wait_for(fired.wait(), 12)
            except TimeoutError as exc:
                await self._check()
                raise BrowserAdapterError("MCP navigation did not finish within its bound") from exc
            await self._check()
            current = await self._evaluate("location.href", context=await self._world())
            if not self.policy.allow(current, document=True):
                self.policy.check()
            return {"url": current}
        finally:
            self.tab.remove_handler(uc.cdp.page.LoadEventFired, loaded)

    @asynccontextmanager
    async def _pause_reader(self):
        """Freeze source tasks across the CDP frame-tree and isolated-read boundary."""
        import nodriver as uc

        await self._check()
        paused = asyncio.Event()
        resumed = asyncio.Event()
        pause_trigger = None
        deadline = asyncio.get_running_loop().time() + 8

        def on_paused(_):
            paused.set()

        def on_resumed(_):
            resumed.set()
            if self._reading_paused:
                self._trip()

        self.tab.add_handler(uc.cdp.debugger.Paused, on_paused)
        self.tab.add_handler(uc.cdp.debugger.Resumed, on_resumed)
        try:
            # Pause acknowledgement is an event, not merely the command response.
            # Source JavaScript cannot create an invisible shadow frame across
            # this boundary. Parser/navigation work also needs the separate
            # complete-document preflight and exact child-document binding.
            async with asyncio.timeout_at(deadline):
                await self._cdp("Debugger.enable")
                await self._cdp("Debugger.pause")
                # pause() waits for the next JavaScript statement on an idle
                # document. This inert owned expression supplies that statement.
                pause_trigger = asyncio.create_task(
                    self._cdp("Runtime.evaluate", {"expression": "void 0"})
                )
                await asyncio.wait_for(paused.wait(), 2)
                if resumed.is_set():
                    self._trip()
                    raise BrowserAdapterError("MCP renderer resumed before its guarded read")
                self._reading_paused = True
                yield
        except TimeoutError as exc:
            self._trip()
            raise BrowserAdapterError("MCP reader pause exceeded its time bound") from exc
        except BaseException:
            # A failed reader must not leave a live renderer paused or uncertain.
            # The caller may close the session after ordinary document rejection.
            if not paused.is_set() or self._tripped:
                self._trip()
            raise
        finally:
            self._reading_paused = False
            try:
                if not self._closed and not self._tripped:
                    try:
                        async with asyncio.timeout_at(deadline):
                            await self._cdp("Debugger.resume")
                            await self._cdp("Debugger.disable")
                    except TimeoutError as exc:
                        self._trip()
                        raise BrowserAdapterError(
                            "MCP reader resume exceeded its time bound"
                        ) from exc
                    except BaseException:
                        self._trip()
                        raise
            finally:
                if pause_trigger is not None:
                    if not pause_trigger.done():
                        pause_trigger.cancel()
                    await asyncio.gather(pause_trigger, return_exceptions=True)
                self.tab.remove_handler(uc.cdp.debugger.Paused, on_paused)
                self.tab.remove_handler(uc.cdp.debugger.Resumed, on_resumed)

    async def scroll(self):
        async with self._pause_reader():
            result = await self._scroll_paused()
        await self._check()
        return result

    async def _scroll_paused(self):
        await self._check()
        context = await self._world()
        current = await self._evaluate("location.href", context=context)
        if not self.policy.allow(current, document=True):
            self.policy.check()
        if classify_page(current, "") != "ok":
            raise BrowserAdapterError("MCP cannot scroll a login or challenge page")
        revision = self.revision
        await self._preflight_frames(context)
        frame_count = await self._frame_count()
        if self.revision != revision:
            raise BrowserAdapterError("MCP frame tree changed before scrolling")
        args = json.dumps({"url": current, "selectors": self.selectors, "frameCount": frame_count})
        security = await self._evaluate(
            f"""(() => {{
            const read = ({READ_SAFE_SNAPSHOT})({args});
            if (!read?.cards || read.cards.tooMany) return false;
            window.scrollBy(0, Math.max(640, innerHeight * 0.8));
            return true;
        }})()""",
            context=context,
            expected_revision=revision,
        )
        if not security:
            raise BrowserAdapterError("MCP security state changed before scrolling")
        await asyncio.sleep(0.25)
        await self._check()
        if self.revision != revision:
            raise BrowserAdapterError("MCP document changed while scrolling")
        return {"scrolled": True}

    async def _frame_count(self):
        tree = await self._cdp("Page.getFrameTree")
        pending = list(tree["frameTree"].get("childFrames", []))
        count = 0
        while pending:
            entry = pending.pop()
            count += 1
            if count > 16:
                raise BrowserAdapterError("MCP frame count exceeded its bound")
            url = entry["frame"]["url"]
            if not url.startswith("about:") and not self.policy.allow(url, document=True):
                self.policy.check()
            pending.extend(entry.get("childFrames", []))
        return count

    async def _preflight_frames(self, context):
        complete = await self._evaluate(
            f"""(() => {{
            const r = ({READ_FRAMES})();
            delete globalThis.__paiyaFramePreflight;
            if (r.failed) return false;
            globalThis.__paiyaFramePreflight = {{document, children: r.documents}};
            return true;
        }})()""",
            context=context,
        )
        if not complete:
            raise BrowserAdapterError("MCP frame security preflight was incomplete")

    async def snapshot(self):
        async with self._pause_reader():
            result = await self._snapshot_paused()
            revision = self.revision
        await self._check()
        if self.revision != revision:
            raise BrowserAdapterError("MCP frame tree changed while resuming the reader")
        return result

    async def _snapshot_paused(self):
        await self._check()
        context = await self._world()
        current = await self._evaluate("location.href", context=context)
        if not self.policy.allow(current, document=True):
            self.policy.check()
        access = classify_page(current, "")
        if access != "ok":
            return {"url": current, "body_text": "", "access": access, "cards_read": False}
        revision = self.revision
        await self._preflight_frames(context)
        frame_count = await self._frame_count()
        if self.revision != revision:
            raise BrowserAdapterError("MCP frame tree changed before card scan")
        token = "paiya_" + uuid.uuid4().hex
        args = json.dumps(
            {"url": current, "selectors": self.selectors, "token": token, "frameCount": frame_count}
        )
        # Isolated-world references bind the exact document, card and image nodes.
        # An observer catches replace-and-restore and style/content mutations even
        # when the final href, bounds and node identity happen to match again.
        script = f"""(args => {{
            const read = ({READ_SAFE_SNAPSHOT})(args);
            if (JSON.stringify(read).length > 1000000)
                return {{body: {{truncated: true}}, cards: null}};
            if (!read?.cards || read.cards.tooMany) return read;
            const cards = Array.from(document.querySelectorAll(args.selectors.post_card));
            const record = {{document, cards, images: cards.map(card =>
                args.selectors.image ? card.querySelector(args.selectors.image) : null),
                children: ({READ_FRAMES})(args.frameCount).documents, changed: false}};
            record.accept = records => {{
                for (const mutation of records) {{
                    const node = mutation.target.nodeType === Node.ELEMENT_NODE ?
                        mutation.target : mutation.target.parentElement;
                    const security = '[role="dialog"],[role="alertdialog"],[role="alert"],'
                        + '[aria-modal="true"],[aria-live="assertive"],form,style,link,'
                        + 'iframe,frame,object,embed';
                    const securityChange = [...mutation.addedNodes, ...mutation.removedNodes]
                        .some(node => node.nodeType === Node.ELEMENT_NODE &&
                            (node.matches(security) || node.querySelector(security)));
                    if (mutation.target.ownerDocument !== document ||
                        securityChange || mutation.type === 'attributes' ||
                        node?.closest('style,link,[role="dialog"],[role="alert"],form') ||
                        cards.some(card => card === node || card.contains(node) ||
                            node?.contains(card))) record.changed = true;
                }}
            }};
            record.observer = new MutationObserver(record.accept);
            record.observer.observe(document.documentElement, {{subtree: true, childList: true,
                attributes: true, characterData: true}});
            for (const child of record.children)
                record.observer.observe(child.documentElement, {{subtree: true, childList: true,
                    attributes: true, characterData: true}});
            globalThis[args.token] = record;
            return read;
        }})({args})"""
        read = await self._evaluate(script, context=context, expected_revision=revision)
        if read and read.get("childDocument"):
            self.policy.failed = True
            raise BrowserAdapterError("MCP does not accept unverified child documents")
        if not read or not read.get("body") or read["body"].get("truncated"):
            raise BrowserAdapterError("MCP security scan was incomplete")
        access = classify_page(current, read["body"]["text"])
        if access != "ok":
            if read.get("cards") is not None:
                raise BrowserAdapterError("MCP challenge gate read cards unexpectedly")
            return {
                "url": current,
                "body_text": read["body"]["text"],
                "access": access,
                "cards_read": False,
            }
        if not read.get("cards") or read["cards"].get("tooMany"):
            raise BrowserAdapterError("MCP card read exceeded its bound")
        try:
            if self.revision != revision:
                raise BrowserAdapterError("MCP frame tree changed during card scan")
            evidence_bytes = len(json.dumps(read).encode("utf-8"))
            for index, card in enumerate(read["cards"]["cards"]):
                card["image"] = None
                card["imageFailed"] = False
                if self.selectors.get("image"):
                    before = await self._image_probe(token, index, current, context)
                    if before is None:
                        card["imageFailed"] = True
                        continue
                    result = await self._cdp(
                        "Page.captureScreenshot",
                        {
                            "format": "jpeg",
                            "quality": 80,
                            "clip": {**before, "scale": 1},
                            "captureBeyondViewport": False,
                        },
                    )
                    after = await self._image_probe(token, index, current, context)
                    data = result.get("data")
                    if isinstance(data, str):
                        evidence_bytes += len(data)
                        if evidence_bytes > MAX_SNAPSHOT_ENCODED_BYTES:
                            raise BrowserAdapterError(
                                "MCP snapshot exceeded its evidence byte budget"
                            )
                    if before != after or not isinstance(data, str) or len(data) > 8_000_000:
                        card["imageFailed"] = True
                    else:
                        card["image"] = data
            await self._check()
            if await self._frame_count() != frame_count or self.revision != revision:
                raise BrowserAdapterError("MCP frame tree changed during evidence capture")
            valid = await self._evaluate(
                f"""(() => {{
                const r = globalThis[{json.dumps(token)}];
                if (r) r.accept(r.observer.takeRecords());
                const frames = ({READ_FRAMES})({frame_count});
                return Boolean(r && r.document === document && !r.changed &&
                    !frames.failed && !frames.challenge &&
                    frames.documents.length === r.children.length &&
                    frames.documents.every((doc, i) => doc === r.children[i]) &&
                    location.href === {json.dumps(current)} &&
                    r.cards.every((card, i) => card.isConnected &&
                        document.querySelectorAll(
                            {json.dumps(self.selectors["post_card"])}
                        )[i] === card));
            }})()""",
                context=context,
            )
            if not valid or self.revision != revision:
                raise BrowserAdapterError(
                    "MCP document or evidence identity changed during capture"
                )
            body = await self._evaluate(f"({_READ_BODY})({args})", context=context)
            if not body or body.get("truncated") or classify_page(current, body["text"]) != "ok":
                raise BrowserAdapterError("MCP security state changed during evidence capture")
            await self._check()
            if self.revision != revision:
                raise BrowserAdapterError("MCP document changed after evidence capture")
            return {
                "url": current,
                "body_text": read["body"]["text"],
                "access": "ok",
                "cards_read": True,
                "cards": read["cards"],
            }
        finally:
            try:
                await self._evaluate(
                    f"globalThis[{json.dumps(token)}]?.observer.disconnect(); "
                    f"delete globalThis[{json.dumps(token)}]",
                    context=context,
                )
            except Exception:
                pass

    async def _image_probe(self, token, index, url, context):
        args = json.dumps({"token": token, "index": index, "url": url, "selectors": self.selectors})
        return await self._evaluate(
            f"""(a => {{
            const r = globalThis[a.token], card = r?.cards[a.index], image = r?.images[a.index];
            if (r) r.accept(r.observer.takeRecords());
            if (!r || r.document !== document || location.href !== a.url || r.changed ||
                !card?.isConnected || !image?.isConnected ||
                document.querySelectorAll(a.selectors.post_card)[a.index] !== card ||
                card.querySelector(a.selectors.image) !== image) return null;
            const b = image.getBoundingClientRect();
            if (b.width < 1 || b.height < 1 || b.width > 2000 || b.height > 2000 ||
                b.left < 0 || b.top < 0 || b.right > innerWidth || b.bottom > innerHeight)
                return null;
            return {{x: b.left + scrollX, y: b.top + scrollY, width: b.width, height: b.height}};
        }})({args})""",
            context=context,
        )

    def status(self):
        if self.policy is None:
            raise BrowserAdapterError("MCP session is not open")
        return {
            "rate_limited": self.policy.limited,
            "retry_after_until": self.policy.deadline.isoformat() if self.policy.deadline else None,
            "manual": self.policy.limited and self.policy.deadline is None,
            "guard_failed": self.policy.failed,
        }

    async def close(self):
        async with self._close_lock:
            return await self._close_owned()

    async def _close_owned(self):
        if self._released:
            return {"closed": True}
        self._closed = True
        for waiter in self._transport_waiters:
            waiter.cancel()
        if self._transport_waiters:
            await asyncio.gather(*self._transport_waiters, return_exceptions=True)
        if self.gate is not None:
            await self.gate.close()
        if self.worker_guard is not None:
            await self.worker_guard.close()
        for task in tuple(self.pending):
            task.cancel()
        if self.pending:
            await asyncio.gather(*tuple(self.pending), return_exceptions=True)
        if self.browser is not None and not self._tripped:
            try:
                await self._cdp("Browser.close", browser=True)
            except BaseException:
                pass
        if self.process is not None and self.process.returncode is None:
            try:
                await asyncio.wait_for(self.process.wait(), 4)
            except TimeoutError:
                # This is the exact child handle from suspended CreateProcess,
                # never a PID found by profile scan, never a process-wide reap.
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), 4)
        if self.browser is not None:
            try:
                if self.tab is not None:
                    await self.tab.disconnect()
                await self.browser.connection.disconnect()
            except Exception:
                pass
        if self.process is not None:
            await self.process.release()
        if self.lease is not None and (self.process is None or self.process.returncode is not None):
            self.lease.__exit__(None, None, None)
        self._released = True
        return {"closed": self.process is None or self.process.returncode is not None}
