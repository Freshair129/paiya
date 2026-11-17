"""Run owned-Chrome/MCP acceptance using localhost fixtures only.

Usage: python scripts/test-mcp-browser.py --runtime-root <new-empty-directory>
Requires the locked mcp extra. Never passes any real profile or source URL.
"""

import argparse
import asyncio
import json
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paiya_collector.mcp_browser import GuardedBrowser
from paiya_collector.mcp_client import TOOLS, McpConnection

SELECTORS = {
    "post_card": "article",
    "post_text": ".message",
    "permalink": "a.permalink",
    "seller_name": ".seller",
    "comment": ".comment",
    "comment_text": ".text",
    "comment_author": ".author",
    "image": ".photo",
    "sort_indicator": "[data-sort]",
    "feed_end": "[data-end]",
    "pinned": "[data-pinned]",
    "suggested": "[data-suggested]",
}
HTML = b"""<div data-sort>Newest posts</div>
<article><a class="permalink" href="https://www.facebook.com/groups/synthetic/posts/123">post</a>
<div class="message">Synthetic RTX 3060 price 5000 THB</div><div class="seller">Fixture seller</div>
<svg class="photo" width="40" height="40"><rect width="40" height="40" fill="red"/></svg></article>
<article><a class="permalink" href="https://www.facebook.com/groups/synthetic/posts/124">post</a>
<div class="message">Synthetic RTX 3070 price 7000 THB</div><div class="seller">Fixture seller</div>
<svg class="photo" width="40" height="40"><rect width="40" height="40" fill="blue"/></svg></article>
<div data-end>end</div><div id="clock">unchanged</div><div id="sibling"></div>"""
hits = []
source_hits = []
limit_sent = None
asset_hits = []
source_cookies = []


class Asset(BaseHTTPRequestHandler):
    def do_GET(self):
        asset_hits.append(dict(self.headers))
        if self.path in {"/302", "/307"}:
            self.send_response(int(self.path[1:]))
            self.send_header("Set-Cookie", "fixture_cookie=asset-poison; Path=/")
            self.send_header("Location", f"http://localhost:{asset_final.server_port}/image")
        else:
            self.send_response(200)
            self.send_header("Set-Cookie", "fixture_cookie=final-poison; Path=/")
            self.send_header("Content-Type", "image/svg+xml")
        self.end_headers()
        if self.path == "/image":
            self.wfile.write(
                b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
                b'<rect width="40" height="40" fill="green"/></svg>'
            )

    def log_message(self, *_):
        pass


class Destination(BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass


class Source(BaseHTTPRequestHandler):
    def do_GET(self):
        global limit_sent
        source_hits.append(self.path)
        source_cookies.append((self.path, self.headers.get("Cookie")))
        if self.path == "/broken-image":
            self.close_connection = True
            return
        if self.path in {"/worker.js", "/sw.js", "/outer-worker.js"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            script = f"fetch('/worker-functional'); fetch('http://localhost:{destination.server_port}/worker-canary')"
            if self.path == "/worker.js":
                script += (
                    ";self.onmessage=e=>fetch(e.data).catch(()=>{});"
                    "self.onconnect=e=>{e.ports[0].onmessage=m=>fetch(m.data).catch(()=>{});"
                    "e.ports[0].start()};"
                )
            if self.path == "/outer-worker.js":
                script = "new Worker('/worker.js')"
            if self.path == "/sw.js":
                script += (
                    ";self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));"
                    "self.addEventListener('activate',e=>e.waitUntil(clients.claim()));"
                    f"self.addEventListener('message',()=>fetch('http://localhost:{destination.server_port}/worker-canary'))"
                )
            self.wfile.write(script.encode())
            return
        if self.path.endswith("redirect-asset"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{asset_first.server_port}/302")
            self.end_headers()
            return
        if self.path.endswith("child-document"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                HTML
                + f'<iframe src="http://localhost:{destination.server_port}/frame"></iframe>'.encode()
            )
            return
        if self.path.endswith("redirect302") or self.path.endswith("redirect307"):
            self.send_response(307 if self.path.endswith("307") else 302)
            self.send_header("Location", f"http://localhost:{destination.server_port}/target")
            self.end_headers()
            return
        if self.path.endswith("limit"):
            limit_sent = datetime.now(UTC)
            self.send_response(429)
            self.send_header("Retry-After", "120")
            self.end_headers()
            self.wfile.write(b"rate limited")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", "fixture_cookie=source-canary; Path=/")
        self.end_headers()
        if self.path.endswith("?child=1"):
            self.wfile.write(
                b"ordinary child" if "frame-neutral" in self.path else b"Security check"
            )
        elif "/groups/frame-" in self.path:
            if self.path.endswith("frame-srcdoc"):
                frame = '<iframe srcdoc="Security check"></iframe>'
            else:
                route = (
                    "/challenge" if self.path.endswith("url-challenge") else self.path + "?child=1"
                )
                frame = f'<iframe src="{route}"></iframe>'
            self.wfile.write(HTML + frame.encode())
        elif self.path in {"/groups/hidden", "/challenge"}:
            self.wfile.write(b'<div role="dialog">Security check</div>')
        elif self.path.endswith("asset302") or self.path.endswith("asset307"):
            code = self.path[-3:]
            image = f'<img class="photo" width="40" height="40" src="http://localhost:{asset_first.server_port}/{code}">'.encode()
            html = HTML.replace(
                b'<svg class="photo" width="40" height="40">'
                b'<rect width="40" height="40" fill="red"/></svg>',
                image,
            )
            self.wfile.write(
                html
                + (
                    f"<script>addEventListener('load',()=>fetch('/verify?case=asset{code}'))</script>"
                ).encode()
            )
        elif self.path.endswith("challenge"):
            self.wfile.write(b'<div role="dialog">Security check</div>' + HTML)
        elif self.path.endswith("cross-resource"):
            self.wfile.write(
                HTML + f'<img src="http://localhost:{destination.server_port}/image">'.encode()
            )
        elif self.path.endswith("failed-resource"):
            self.wfile.write(HTML + b'<img src="/broken-image">')
        else:
            self.wfile.write(HTML)

    def log_message(self, *_):
        pass


def profile(path):
    path.mkdir()
    (path / "PAIYA_COLLECTOR_PROFILE").write_text("owner-group-01")
    (path / "PAIYA_SYNTHETIC_PROFILE").write_text("synthetic-only")
    (path / "sentinel").write_text("preserve")
    return path


def connection_case(root, case):
    path = profile(root / case)
    url = f"http://localhost:{source.server_port}/groups/{case}"
    with McpConnection() as connection:
        assert TOOLS == {
            tool.name
            for tool in asyncio.run_coroutine_threadsafe(
                connection.session.list_tools(), connection.loop
            )
            .result()
            .tools
        }
        asset_start = len(asset_hits)
        opened = connection.call(
            "collector_open",
            {
                "profile_path": str(path),
                "group_url": url,
                "selectors": SELECTORS,
                "synthetic": True,
                "asset_origins": [
                    f"http://localhost:{asset_first.server_port}",
                    f"http://localhost:{asset_final.server_port}",
                ]
                if case in {"asset302", "asset307", "redirect-asset"}
                else [],
            },
        )
        assert opened["opened"]
        if case.startswith("redirect") or case == "child-document":
            try:
                connection.call("collector_navigate")
                raise AssertionError("cross-origin redirect was accepted")
            except RuntimeError:
                pass
            assert not hits
            assert len(asset_hits) == asset_start
        elif case == "limit":
            from paiya_collector.browser_adapter import RateLimitPause

            try:
                connection.call("collector_navigate")
                raise AssertionError("429 was accepted")
            except RateLimitPause as pause:
                seconds = (pause.retry_after_until - limit_sent).total_seconds()
                assert 119 <= seconds <= 125, seconds
                assert not pause.manual_resume_required
        else:
            connection.call("collector_navigate")
            snap = connection.call("collector_snapshot")
            if case == "challenge":
                assert not snap["cards_read"] and snap["access"] == "challenge_required"
                assert "RTX" not in json.dumps(snap)
            else:
                assert snap["cards_read"] and len(snap["cards"]["cards"]) == 2
                assert all(
                    card["image"] and not card["imageFailed"] for card in snap["cards"]["cards"]
                )
                assert not hits
                if case in {"asset302", "asset307"}:
                    assert len(asset_hits) == asset_start + 2
                    for headers in asset_hits[asset_start:]:
                        assert not {key.lower() for key in headers} & {
                            "cookie",
                            "authorization",
                            "proxy-authorization",
                            "referer",
                            "x-csrf-token",
                        }
                    verify = f"/verify?case={case}"
                    deadline = time.monotonic() + 5
                    while not any(route == verify for route, _ in source_cookies):
                        assert time.monotonic() < deadline, (
                            "missing source verification",
                            source_hits,
                        )
                        connection.call("collector_status")
                        time.sleep(0.05)
                    assert [cookie for route, cookie in source_cookies if route == verify][-1] == (
                        "fixture_cookie=source-canary"
                    )
    assert (path / "sentinel").read_text() == "preserve"
    print(f"PASS MCP {case}", flush=True)


async def identity_case(root, mode):
    path = profile(root / mode)
    browser = GuardedBrowser()
    url = f"http://localhost:{source.server_port}/groups/{mode}"
    await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
    try:
        await browser.navigate(url)
        original = browser._cdp
        changed = False

        async def cdp(method, params=None, **kwargs):
            nonlocal changed
            result = await original(method, params, **kwargs)
            if method == "Page.captureScreenshot" and not changed:
                changed = True
                if mode == "same-box-node-replacement":
                    await original(
                        "Runtime.evaluate",
                        {
                            "expression": "const node=document.querySelector('.photo');"
                            "node.replaceWith(node.cloneNode(true))"
                        },
                    )
                elif mode == "same-url-reload":
                    await original("Page.navigate", {"url": url})
                    await asyncio.sleep(0.3)
                elif mode == "same-card-replacement":
                    await original(
                        "Runtime.evaluate",
                        {
                            "expression": "const node=document.querySelector('article');"
                            "node.replaceWith(node.cloneNode(true))"
                        },
                    )
                elif mode == "unrelated-timer":
                    await original(
                        "Runtime.evaluate",
                        {
                            "expression": "document.getElementById('clock').textContent="
                            "'timer advanced'"
                        },
                    )
                else:
                    await original(
                        "Runtime.evaluate",
                        {
                            "expression": "document.getElementById('sibling').innerHTML="
                            "'<div role=dialog>Security check</div>'"
                        },
                    )
            return result

        browser._cdp = cdp
        try:
            snapshot = await browser.snapshot()
            assert mode == "unrelated-timer", "stale evidence was accepted"
            assert snapshot["cards_read"]
        except RuntimeError:
            assert mode != "unrelated-timer", "unrelated timer rejected a stable card"
        assert changed
    finally:
        await browser.close()
    print(f"PASS CDP {mode}", flush=True)


async def lifecycle_case(root, mode):
    path = profile(root / mode)
    browser = GuardedBrowser()
    url = f"http://localhost:{source.server_port}/groups/{mode}"
    await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
    try:
        await browser.navigate(url)
        if mode == "new-tab":
            before = len(hits)
            try:
                await browser._cdp(
                    "Runtime.evaluate",
                    {
                        "expression": f"window.open('http://localhost:{destination.server_port}/popup')",
                        "userGesture": True,
                    },
                )
            except RuntimeError:
                pass  # Chrome may keep window.open pending while its target is paused/closed.
            await asyncio.sleep(0.5)
            assert len(hits) == before, "new target contacted a denied origin"
            try:
                await browser.snapshot()
                raise AssertionError("new target left the capture usable")
            except RuntimeError:
                pass
        elif mode == "profile-reopen":
            old_pid = browser.process.pid
            await browser.close()
            browser = GuardedBrowser()
            await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
            assert browser.process.pid != old_pid
            await browser.navigate(url)
            assert (await browser.snapshot())["cards_read"]
        else:
            import paiya_collector.mcp_browser as module

            budget = module.MAX_SNAPSHOT_ENCODED_BYTES
            module.MAX_SNAPSHOT_ENCODED_BYTES = 1
            try:
                try:
                    await browser.snapshot()
                    raise AssertionError("evidence budget did not stop capture")
                except RuntimeError as exc:
                    assert "byte budget" in str(exc)
            finally:
                module.MAX_SNAPSHOT_ENCODED_BYTES = budget
    finally:
        await browser.close()
    print(f"PASS CDP {mode}", flush=True)


async def frame_case(root, mode):
    from paiya_collector import mcp_browser

    path = profile(root / mode)
    browser = GuardedBrowser()
    url = f"http://localhost:{source.server_port}/groups/{mode}"
    original = mcp_browser.READ_SAFE_SNAPSHOT
    needle = "const cards = all(document, selectors.post_card);"
    assert needle in original
    mcp_browser.READ_SAFE_SNAPSHOT = original.replace(
        needle, "globalThis.cardReadTrap = true; " + needle
    )
    try:
        await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
        await browser.navigate(url)
        result = await browser.snapshot()
        context = await browser._world()
        trapped = await browser._evaluate("globalThis.cardReadTrap === true", context=context)
        if mode == "frame-neutral":
            assert result["cards_read"] and trapped
        else:
            assert result["access"] != "ok" and not result["cards_read"] and not trapped
    finally:
        mcp_browser.READ_SAFE_SNAPSHOT = original
        await browser.close()
    print(f"PASS child security before card scan: {mode}", flush=True)


async def fault_case(root, mode):
    from paiya_collector.mcp_browser import MAX_PENDING_EVENTS

    path = profile(root / mode)
    browser = GuardedBrowser()
    url = f"http://localhost:{source.server_port}/groups/faults"
    await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
    peak = 0
    try:
        await browser.navigate(url)
        before = len(source_hits)
        if mode == "event-overflow":
            original = browser._cdp
            held = asyncio.Event()

            async def hold_request(method, params=None, **kwargs):
                if method == "Fetch.continueRequest":
                    await held.wait()  # Fixture seam: keep request events pending.
                return await original(method, params, **kwargs)

            browser._cdp = hold_request
            await browser._evaluate("for(let i=0;i<200;i++) fetch('/burst?i='+i).catch(()=>{})")
        else:
            await browser._evaluate("setTimeout(()=>fetch('/idle-canary').catch(()=>{}),500)")
            connection = (
                browser.tab if mode == "page-transport-loss" else browser.browser.connection
            )
            connection.websocket.transport.abort()
        # No Collector/CDP RPC follows the transport loss: idle failure must close
        # the network gate and owned process without waiting for the next call.
        deadline = time.monotonic() + 6
        while browser.process.returncode is None and time.monotonic() < deadline:
            peak = max(peak, len(browser.pending))
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.6)
        assert browser.policy.failed and not browser.gate.armed
        assert browser.process.returncode is not None, (
            "idle guard failure did not stop owned Chrome"
        )
        assert "/idle-canary" not in source_hits[before:]
        assert not any(route.startswith("/burst?") for route in source_hits[before:])
        assert peak <= MAX_PENDING_EVENTS
    finally:
        await browser.close()
    assert not browser.pending
    print(f"PASS idle fault containment: {mode}; peak_pending={peak}", flush=True)


async def worker_case(root, mode):
    path = profile(root / mode)
    url = f"http://localhost:{source.server_port}/groups/workers"
    browser = GuardedBrowser()
    before = len(source_hits)
    denied_before = len(hits)
    try:
        await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
        await browser.navigate(url)
        paused = browser._handle_paused
        observed = []

        async def observe_request(event, **kwargs):
            observed.append((event.request.url, event.response_status_code))
            await paused(event, **kwargs)

        browser._handle_paused = observe_request
        if mode.startswith("worker-dedicated"):
            expression = "globalThis.fixtureWorker = new Worker('/worker.js')"
        elif mode.startswith("worker-shared"):
            expression = "globalThis.fixtureWorker = new SharedWorker('/worker.js')"
        elif mode == "worker-blob":
            script = f"fetch('http://localhost:{source.server_port}/worker-functional'); fetch('http://localhost:{destination.server_port}/worker-canary')"
            expression = (
                "globalThis.fixtureWorker = new Worker(URL.createObjectURL(new Blob(["
                + json.dumps(script)
                + "],{type:'application/javascript'})))"
            )
        elif mode == "worker-nested":
            expression = "globalThis.fixtureWorker = new Worker('/outer-worker.js')"
        else:
            expression = "navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{})"
        await browser._evaluate(expression)
        deadline = time.monotonic() + 12
        while "/worker-functional" not in source_hits[before:]:
            browser.policy.check()
            assert time.monotonic() < deadline, "guarded worker did not run"
            await asyncio.sleep(0.05)
        if mode.startswith("worker-persisted"):
            await asyncio.sleep(0.3)
            await browser.close()
            browser = GuardedBrowser()
            before = len(source_hits)
            await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
            assert not source_hits[before:]
            await browser.navigate(url)
            await browser._evaluate(
                "navigator.serviceWorker.ready.then(r=>r.active.postMessage('go'))"
            )
        await asyncio.sleep(0.5)
        browser.policy.check()
        if mode.startswith(("worker-dedicated", "worker-shared")):
            assert source_hits[before:].count("/worker-functional") == 1
            assert any(
                url.endswith("/worker-functional") and status == 200 for url, status in observed
            ), "browser-level guard did not see worker response"
            if mode.endswith(("302", "307", "limit")):
                route = (
                    "/worker-limit" if mode.endswith("limit") else "/worker-redirect" + mode[-3:]
                )
                target = (
                    "fixtureWorker.port" if mode.startswith("worker-shared") else "fixtureWorker"
                )
                await browser._evaluate(target + ".postMessage(" + json.dumps(route) + ")")
                deadline = time.monotonic() + 8
                while not any(
                    url.endswith(route) and status is not None for url, status in observed
                ):
                    assert time.monotonic() < deadline, "worker probe response was not guarded"
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.1)
                assert source_hits[before:].count(route) == 1
                if mode.endswith("limit"):
                    assert browser.policy.limited and browser.policy.deadline is not None
                    try:
                        await browser.snapshot()
                        raise AssertionError("worker 429 admitted snapshot")
                    except RuntimeError:
                        pass
                    print(f"PASS guarded worker canary: {mode}", flush=True)
                    return
        assert "/worker-canary" not in hits[denied_before:]
        assert "/target" not in hits[denied_before:]
        assert (await browser.snapshot())["cards_read"]
    finally:
        await browser.close()
    assert (path / "sentinel").read_text() == "preserve"
    print(f"PASS guarded worker canary: {mode}", flush=True)


async def eof_case(root, forced=False):
    import psutil

    path = profile(root / ("forced-service-kill" if forced else "transport-eof"))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "paiya_collector.mcp_server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )

    async def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    async def receive():
        return json.loads(await asyncio.wait_for(process.stdout.readline(), 45))

    await send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "synthetic-eof", "version": "1"},
            },
        }
    )
    assert (await receive())["id"] == 1
    await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await send(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "collector_open",
                "arguments": {
                    "profile_path": str(path),
                    "group_url": f"http://localhost:{source.server_port}/groups/eof",
                    "selectors": SELECTORS,
                    "synthetic": True,
                },
            },
        }
    )
    result = await receive()
    response = result["result"]
    opened = response.get("structuredContent") or json.loads(
        "".join(item["text"] for item in response["content"] if item["type"] == "text")
    )
    assert opened["ok"], opened
    pid = opened["result"]["pid"]
    owned = [psutil.Process(pid), *psutil.Process(pid).children(recursive=True)]
    if forced:
        process.kill()  # Exact subprocess handle created by this test only.
    else:
        process.stdin.close()
    await asyncio.wait_for(process.wait(), 25)
    deadline = time.monotonic() + 5
    while any(child.is_running() for child in owned) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not any(child.is_running() for child in owned), "owned Chrome tree survived service exit"
    assert (path / "sentinel").read_text() == "preserve"
    print(
        f"PASS MCP {'forced kill' if forced else 'transport EOF'} closes owned Chrome tree",
        flush=True,
    )


async def startup_case(root):
    from paiya_collector.mcp_process import OwnedChromeProcess

    path = profile(root / "restored-startup")
    url = f"http://localhost:{source.server_port}/groups/restored-startup"
    browser = GuardedBrowser()
    await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
    await browser.navigate(url)
    await browser.close()
    before = len(source_hits)
    descriptor = OwnedChromeProcess.__dict__["start"]
    original = OwnedChromeProcess.start

    async def restoring_start(args):
        assert args[-1] == "about:blank"
        # Reuse the populated synthetic profile and inject an unwanted startup
        # navigation at the launch seam, before the CDP guard can exist.
        created = await original(args[:-1] + [url])
        browser.process = created
        deadline = time.monotonic() + 5
        while browser.gate.blocked_source == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return created

    OwnedChromeProcess.start = staticmethod(restoring_start)
    browser = GuardedBrowser()
    try:
        try:
            await browser.open(path, group_url=url, selectors=SELECTORS, synthetic=True)
            raise AssertionError("restored source tab was accepted")
        except RuntimeError:
            pass
        assert browser.gate.blocked_source > 0, "test did not exercise a pre-arm source request"
        assert len(source_hits) == before, "Chrome bypassed the pre-arm proxy gate"
        assert browser.process.returncode is not None
    finally:
        OwnedChromeProcess.start = descriptor
        await browser.close()
    assert (path / "sentinel").read_text() == "preserve"
    print("PASS reused profile startup navigation cannot contact source before guard", flush=True)


async def job_error_case():
    import psutil
    import win32job
    import win32process
    from paiya_collector.mcp_process import OwnedChromeProcess

    original_create = win32process.CreateProcess
    original_assign = win32job.AssignProcessToJobObject
    owned = []

    def create(*args, **kwargs):
        result = original_create(*args, **kwargs)
        owned.append(psutil.Process(result[2]))
        return result

    def fail_assign(*_):
        raise RuntimeError("synthetic assign failure")

    win32process.CreateProcess = create
    win32job.AssignProcessToJobObject = fail_assign
    try:
        try:
            await OwnedChromeProcess.start([sys.executable, "-c", "import time; time.sleep(60)"])
            raise AssertionError("assignment failure was ignored")
        except RuntimeError as exc:
            assert "synthetic assign failure" in str(exc)
        assert owned and not owned[0].is_running()
    finally:
        win32process.CreateProcess = original_create
        win32job.AssignProcessToJobObject = original_assign
    print("PASS failed Job assignment retires only its suspended child", flush=True)


def cli_case(root, case):
    import subprocess

    local = root / ("cli-" + case)
    local.mkdir()
    path = profile(local / "profile")
    config = {
        "synthetic": True,
        "group_key": "owner-group-01",
        "grant_id": "44444444-5555-4666-8777-888888888888",
        "group_url": f"http://localhost:{source.server_port}/groups/{case}",
        "identity_scope": "synthetic",
        "adapter_version": "paiya-mcp-synthetic/0.1.0",
        "grant": {"active": True, "scopes": ["capture", "read", "media"], "retention_days": 7},
        "post_markers_verified": True,
        "page_size": 5,
        "overlap": 3,
        "scroll_budget": 0,
        "time_budget_seconds": 30,
        "selectors": SELECTORS,
    }
    config_path = local / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "paiya_collector.cli",
        "discover-mcp-synthetic",
        "--group-config",
        str(config_path),
        "--profile-dir",
        str(path),
        "--inbox",
        str(local / "inbox"),
        "--state-dir",
        str(local / "state"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=70)
    (local / "stdout.json").write_text(result.stdout, encoding="utf-8")
    (local / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    report = json.loads(result.stdout)
    if case == "limit":
        assert result.returncode == 2 and report["reason"] == "http_429", report
        assert not list(local.glob("inbox/*/READY"))
        # Restart should remain paused and never start another browser/request.
        before = len(source_hits)
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert repeated.returncode == 2 and len(source_hits) == before
    else:
        assert result.returncode == 0, (report, result.stderr)
        expected = 0 if case == "challenge" else 2
        assert report["cards_parsed"] == expected, report
        assert (Path(report["bundle_path"]) / "READY").exists()
    print(f"PASS CLI {case}", flush=True)


def wrapper_case(root):
    import shutil
    import subprocess

    local = root / "wrapper"
    scripts = local / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(Path(__file__).with_name("run-collector.ps1"), scripts / "run-collector.ps1")
    runner = local / "probe.ps1"
    runner.write_text(
        "param([string]$CaseFile)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "function global:uv { $args | ConvertTo-Json -Compress; $global:LASTEXITCODE = 0 }\n"
        "$arguments = @(Get-Content -LiteralPath $CaseFile -Raw | ConvertFrom-Json)\n"
        "& (Join-Path $PSScriptRoot 'scripts/run-collector.ps1') @arguments\n",
        encoding="utf-8",
    )
    cases = [
        (["discover"], None),
        (["discover-browser"], "browser"),
        (["discover-browser", "--transport", "playwright"], "browser"),
        (["discover-browser", "--transport", "mcp"], "mcp"),
        (["discover-browser", "--transport=mcp"], "mcp"),
        (["discover-browser", "--transport=mcp", "--transport", "playwright"], "browser"),
        (["discover-browser", "--transport", "playwright", "--transport=mcp"], "mcp"),
        (["discover-browser", "--group-config", "mcp", "--transport=invalid"], "browser"),
        (["discover-mcp-synthetic"], "mcp"),
    ]
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "Windows PowerShell is required for wrapper acceptance"
    for index, (arguments, expected) in enumerate(cases):
        case_file = local / f"case-{index}.json"
        case_file.write_text(json.dumps(arguments), encoding="utf-8")
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(runner), str(case_file)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        forwarded = json.loads(result.stdout)
        extras = [forwarded[i + 1] for i, value in enumerate(forwarded) if value == "--extra"]
        assert extras == ([expected] if expected else []), (arguments, forwarded)
        assert forwarded[-len(arguments) :] == arguments
        assert "--locked" in forwarded and "--no-dev" in forwarded
    print("PASS PowerShell wrapper selects only intended extras (9 argument cases)", flush=True)


class FixtureServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(
            sys.exception(), (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
        ):
            return  # Expected when a guard or owned-process test closes an in-flight request.
        super().handle_error(request, client_address)


parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", type=Path, required=True)
parser.add_argument(
    "--case",
    default="all",
    choices=(
        "all",
        "mcp",
        "identity",
        "lifecycle",
        "startup",
        "cli",
        "wrapper",
        "frames",
        "workers",
        "faults",
    ),
)
args = parser.parse_args()
args.runtime_root.mkdir(parents=True, exist_ok=False)
destination = FixtureServer(("127.0.0.1", 0), Destination)
source = FixtureServer(("127.0.0.1", 0), Source)
asset_first = FixtureServer(("127.0.0.1", 0), Asset)
asset_final = FixtureServer(("127.0.0.1", 0), Asset)
servers = (source, destination, asset_first, asset_final)
threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
for thread in threads:
    thread.start()
started = time.monotonic()
try:
    if args.case in {"all", "wrapper"}:
        wrapper_case(args.runtime_root)
    if args.case in {"all", "mcp"}:
        for case in (
            "feed",
            "redirect302",
            "redirect307",
            "limit",
            "challenge",
            "cross-resource",
            "failed-resource",
            "asset302",
            "asset307",
            "redirect-asset",
            "child-document",
        ):
            connection_case(args.runtime_root, case)
    if args.case in {"all", "identity"}:
        for case in (
            "same-box-node-replacement",
            "same-url-reload",
            "same-card-replacement",
            "unrelated-timer",
            "sibling-overlay",
        ):
            asyncio.run(identity_case(args.runtime_root, case))
    if args.case in {"all", "frames"}:
        for case in (
            "frame-url-challenge",
            "frame-content-challenge",
            "frame-srcdoc",
            "frame-neutral",
        ):
            asyncio.run(frame_case(args.runtime_root, case))
    if args.case in {"all", "workers"}:
        for case in (
            "worker-dedicated",
            "worker-shared",
            "worker-dedicated-302",
            "worker-dedicated-307",
            "worker-dedicated-limit",
            "worker-shared-302",
            "worker-shared-307",
            "worker-shared-limit",
            "worker-blob",
            "worker-nested",
            "worker-service",
            "worker-persisted-stopped",
            "worker-persisted-active",
        ):
            asyncio.run(worker_case(args.runtime_root, case))
    if args.case in {"all", "faults"}:
        for case in ("page-transport-loss", "browser-transport-loss", "event-overflow"):
            asyncio.run(fault_case(args.runtime_root, case))
    if args.case in {"all", "lifecycle"}:
        for case in ("new-tab", "evidence-budget", "profile-reopen"):
            asyncio.run(lifecycle_case(args.runtime_root, case))
        asyncio.run(eof_case(args.runtime_root))
        asyncio.run(eof_case(args.runtime_root, forced=True))
        asyncio.run(job_error_case())
    if args.case in {"all", "lifecycle", "startup"}:
        asyncio.run(startup_case(args.runtime_root))
    if args.case in {"all", "cli"}:
        for case in ("feed", "limit", "challenge"):
            cli_case(args.runtime_root, case)
    print(
        json.dumps(
            {
                "ok": True,
                "seconds": round(time.monotonic() - started, 2),
                "destination_requests": len(hits),
            }
        ),
        flush=True,
    )
finally:
    for server in servers:
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join()
