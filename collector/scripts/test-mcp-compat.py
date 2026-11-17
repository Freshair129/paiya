"""Synthetic-only compatibility acceptance; every HTTP server is loopback."""

import argparse
import asyncio
import gzip
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paiya_collector.mcp_browser import GuardedBrowser
from paiya_collector.state import load_state

SELECTORS = {"post_card": "article", "post_text": ".message", "permalink": "a", "image": ""}
HTML = (
    b'<article><a href="https://www.facebook.com/groups/synthetic/posts/1">post</a>'
    b'<div class="message">Synthetic GPU 5000 THB</div>'
    b'<svg class="photo" width="40" height="40"><rect width="40" height="40" fill="red"/>'
    b'</svg></article><div id="shadow-host"></div>'
)
hits = []
asset_hits = []
script_limit = False
script_redirect = False
delayed_response = threading.Event()


class Asset(BaseHTTPRequestHandler):
    def do_GET(self):
        asset_hits.append((self.path, dict(self.headers)))
        if self.path == "/requires-cookie":
            self.send_response(401)
            self.send_header("Set-Cookie", "fixture_cookie=cdn-poison; SameSite=None; Secure")
            self.end_headers()
            return
        body = gzip.compress(b"globalThis.moduleLoaded = true; export const fixture=1;")
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript")
        self.send_header("Content-Encoding", "gzip")
        if self.path != "/cors-denied":
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Set-Cookie", "fixture_cookie=cdn-poison; SameSite=None; Secure; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Source(BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append((self.path, dict(self.headers)))
        if self.path == "/groups/compat/child-delayed":
            delayed_response.wait(4)
        if self.path.startswith("/sw.js") and script_redirect:
            self.send_response(307)
            self.send_header("Location", f"http://localhost:{assets.server_port}/script-denied")
            self.end_headers()
            return
        if self.path == "/worker-limit" or (
            self.path.startswith("/sw.js") and (args.case == "worker-script-limit" or script_limit)
        ):
            self.send_response(429)
            self.send_header("Retry-After", "120")
            self.end_headers()
            return
        if self.path == "/worker-redirect":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{assets.server_port}/worker-denied")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", "fixture_cookie=source-canary; Path=/; SameSite=Lax")
        if self.path.startswith("/sw.js"):
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Service-Worker-Allowed", "/")
            body = (
                b"fetch('/worker-start').catch(()=>{});"
                b"self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));"
                b"self.addEventListener('activate',e=>e.waitUntil(clients.claim()));"
                b"self.addEventListener('message',e=>{if(e.data==='idle'){"
                b"setTimeout(()=>fetch('/worker-idle-canary'),1500);"
                b"e.source.postMessage('scheduled');return;}"
                b"e.waitUntil(fetch(e.data||'/worker-ok')"
                b".then(()=>e.source.postMessage('worker-ok'))"
                b".catch(()=>e.source.postMessage('denied')));});"
                b"self.addEventListener('fetch',e=>{"
                b"const p=new URL(e.request.url).pathname;"
                b"if(p.endsWith('/sw-429')) e.respondWith(new Response('limited',"
                b"{status:429,headers:{'Retry-After':'120'}}));"
                b"if(p.endsWith('/sw-challenge')) e.respondWith(new Response('Security check',"
                b"{headers:{'Content-Type':'text/html'}}));});"
            )
        elif self.path == "/groups/compat/child-delayed":
            body = b'<iframe srcdoc="Security check"></iframe>'
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/with-delayed-frame":
            body = HTML + b'<iframe src="/groups/compat/child-delayed"></iframe>'
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/child":
            body = b"ordinary child"
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/challenge":
            body = b"Security check"
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/frame":
            body = HTML + b'<iframe src="/groups/compat/child"></iframe>'
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/frame-challenge":
            body = HTML + b'<iframe src="/groups/compat/challenge"></iframe>'
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/frame-shadow":
            body = HTML + (
                b'<div id="host"></div><script>'
                b"document.querySelector('#host').attachShadow({mode:'closed'}).innerHTML="
                b"'<iframe srcdoc=\"Security check\"></iframe>';</script>"
            )
            self.send_header("Content-Type", "text/html")
        elif self.path == "/groups/compat/frame-opaque":
            body = HTML + b'<iframe sandbox srcdoc="Security check"></iframe>'
            self.send_header("Content-Type", "text/html")
        elif self.path.startswith("/groups/compat/cdn"):
            resource = (
                "/requires-cookie"
                if self.path.endswith("credential")
                else "/cors-denied"
                if self.path.endswith("denied")
                else "/module.js"
            )
            body = (
                HTML
                + f'<script type="module" src="http://localhost:{assets.server_port}{resource}"></script>'.encode()
            )
            self.send_header("Content-Type", "text/html")
        else:
            body = HTML
            self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


async def wait_js(browser, expression):
    deadline = time.monotonic() + 8
    while not await browser._evaluate(expression):
        browser.policy.check()
        assert time.monotonic() < deadline, (
            expression,
            browser.worker_guard.sessions,
            browser.worker_guard.guarded,
            [p for p, _ in hits],
        )
        await asyncio.sleep(0.05)


async def case(root, name):
    global script_limit, script_redirect
    script_limit = name == "worker-script-limit"
    script_redirect = name == "worker-script-redirect"
    asset_start = len(asset_hits)
    profile = root / name
    profile.mkdir()
    (profile / "PAIYA_COLLECTOR_PROFILE").write_text("owner-group-01")
    (profile / "PAIYA_SYNTHETIC_PROFILE").write_text("synthetic-only")
    (profile / "sentinel").write_text("preserve")
    url = f"http://localhost:{source.server_port}/groups/compat"
    browser = GuardedBrowser()
    try:
        await browser.open(
            profile,
            group_url=url,
            selectors=SELECTORS,
            synthetic=True,
            asset_origins=[f"http://localhost:{assets.server_port}"]
            if name.startswith("cdn")
            else [],
        )
        try:
            suffix = (
                "/frame"
                if name == "frame-child-navigation"
                else ("/" + name if not name.startswith("worker") else "")
            )
            await browser.navigate(url + suffix)
        except RuntimeError:
            if name == "frame-opaque":
                assert browser.policy.failed
                print("PASS compatibility frame-opaque: refused before snapshot", flush=True)
                return
            if name != "cdn-credential":
                raise
            assert browser.policy.failed
            assert len(asset_hits[asset_start:]) == 1
            assert "Cookie" not in asset_hits[-1][1]
            print("PASS compatibility cdn-credential", flush=True)
            return
        assert name != "cdn-credential", "credential-dependent CDN did not fail closed"
        if name.startswith("reader-"):
            browser.selectors["image"] = ".photo"
            original_cdp = browser._cdp
            injected = False
            screenshots = 0
            scheduled = name == "reader-source-task"
            delayed_frame = name == "reader-delayed-frame"
            delayed_navigation = None
            preflight_seen = False
            if delayed_frame:
                delayed_navigation = asyncio.create_task(
                    browser.navigate(url + "/with-delayed-frame")
                )
                deadline = time.monotonic() + 8
                while not any(p == "/groups/compat/child-delayed" for p, _ in hits):
                    assert time.monotonic() < deadline, "delayed frame request never started"
                    await asyncio.sleep(0.01)

            async def guarded_cdp(method, params=None, **kwargs):
                nonlocal injected, screenshots, preflight_seen
                if delayed_frame and method == "Runtime.evaluate":
                    expression = (params or {}).get("expression", "")
                    assert "const record =" not in expression, "loading frame reached card read"
                    if "__paiyaFramePreflight =" in expression:
                        assert browser._reading_paused
                        preflight_seen = True
                if method == "Debugger.pause" and name == "reader-pause-timeout":
                    injected = True
                    return {}
                if method == "Debugger.resume" and name == "reader-resume-timeout":
                    injected = True
                    await asyncio.sleep(10)
                result = await original_cdp(method, params, **kwargs)
                if method == "Page.captureScreenshot":
                    screenshots += 1
                if method == "Page.getFrameTree" and not injected:
                    injected = name != "reader-resume-timeout"
                    assert browser._reading_paused
                    if scheduled:
                        await original_cdp(
                            "Runtime.evaluate",
                            {
                                "expression": "globalThis.sourceWrites=0; setTimeout(()=>{"
                                "sourceWrites++; document.querySelector('#shadow-host')"
                                ".attachShadow({mode:'closed'}).innerHTML="
                                "'<iframe srcdoc=\"Security check\"></iframe>';},0)"
                            },
                        )
                        await asyncio.sleep(0.2)
                        assert await browser._evaluate("sourceWrites") == 0
                    elif name == "reader-unexpected-resume":
                        await original_cdp("Debugger.resume")
                        await asyncio.sleep(0.1)
                    elif name == "reader-transport-loss":
                        await browser.tab.websocket.close()
                        await asyncio.sleep(0.1)
                return result

            browser._cdp = guarded_cdp
            if scheduled:
                try:
                    await browser.snapshot()
                except RuntimeError:
                    # A source task that runs at resume may invalidate the result.
                    pass
                assert injected
                await wait_js(browser, "globalThis.sourceWrites === 1")
                assert not browser._reading_paused
                try:
                    await browser.snapshot()
                    raise AssertionError("resumed source challenge was admitted")
                except RuntimeError:
                    pass
            elif delayed_frame:
                try:
                    await browser.snapshot()
                    raise AssertionError("loading frame was admitted")
                except RuntimeError:
                    pass
                assert preflight_seen and not injected and screenshots == 0
                delayed_response.set()
                await asyncio.gather(delayed_navigation, return_exceptions=True)
            else:
                try:
                    await browser.snapshot()
                    raise AssertionError("lost pause boundary returned a snapshot")
                except RuntimeError:
                    pass
                assert injected and browser._tripped and not browser.gate.armed
                if name != "reader-resume-timeout":
                    assert screenshots == 0
                await browser.close()
                assert browser.process.returncode is not None
            print(f"PASS compatibility {name}", flush=True)
            return
        if name == "frame-child-navigation":
            browser.selectors["image"] = ".photo"
            original_cdp = browser._cdp
            injected = False
            card_calls = 0
            screenshots = 0

            async def navigation_race(method, params=None, **kwargs):
                nonlocal injected, card_calls, screenshots
                if method == "Runtime.evaluate" and "const record =" in (params or {}).get(
                    "expression", ""
                ):
                    card_calls += 1
                if method == "Page.captureScreenshot":
                    screenshots += 1
                result = await original_cdp(method, params, **kwargs)
                if method == "Page.getFrameTree" and not injected:
                    injected = True
                    child = result["frameTree"]["childFrames"][0]["frame"]["id"]
                    await original_cdp(
                        "Page.navigate", {"url": url + "/challenge", "frameId": child}
                    )
                    await asyncio.sleep(0.3)
                return result

            browser._cdp = navigation_race
            try:
                await browser.snapshot()
                raise AssertionError("child navigation race returned a snapshot")
            except RuntimeError:
                pass
            assert injected and card_calls == 0 and screenshots == 0
            print("PASS compatibility frame-child-navigation", flush=True)
            return
        if name.startswith("frame-race"):
            browser.selectors["image"] = ".photo"
            original_cdp = browser._cdp
            original_evaluate = browser._evaluate
            injected = False
            screenshots = 0
            initial_revision = browser.revision
            mode = "closed" if name.endswith("closed") else "open"
            insertion = (
                "const host=document.createElement('div'); document.body.append(host);"
                "host.attachShadow({mode:" + json.dumps(mode) + "}).innerHTML="
                "'<iframe srcdoc=\"Security check\"></iframe>'"
            )

            async def race_cdp(method, params=None, **kwargs):
                nonlocal injected, screenshots
                result = await original_cdp(method, params, **kwargs)
                if method == "Page.captureScreenshot":
                    screenshots += 1
                if method == "Page.getFrameTree" and "barrier" in name and not injected:
                    injected = True
                    await original_cdp("Runtime.evaluate", {"expression": insertion})
                    await asyncio.sleep(0.05)
                return result

            async def race_evaluate(expression, **kwargs):
                nonlocal injected
                if "const record =" in expression:
                    needle = "const cards = all(document, selectors.post_card);"
                    assert needle in expression
                    expression = expression.replace(
                        needle, "globalThis.cardReadTrap=true;" + needle
                    )
                    if "evaluate" in name and not injected:
                        injected = True
                        await original_cdp("Runtime.evaluate", {"expression": insertion})
                        await asyncio.sleep(0.05)
                return await original_evaluate(expression, **kwargs)

            browser._cdp = race_cdp
            browser._evaluate = race_evaluate
            try:
                await browser.snapshot()
                raise AssertionError("racing shadow frame returned cards or images")
            except RuntimeError as exc:
                assert any(word in str(exc).lower() for word in ("frame", "document")), str(exc)
            assert injected and browser.revision > initial_revision
            context = await browser._world()
            internal_read = await original_evaluate(
                "globalThis.cardReadTrap === true", context=context
            )
            print(
                f"RACE {name}: snapshot refused; internal_cards={internal_read}; "
                f"screenshots={screenshots}",
                flush=True,
            )
            assert not internal_read, "challenge frame was not checked before card read"
            assert screenshots == 0, "challenge frame was not checked before image capture"
            print(f"PASS compatibility {name}", flush=True)
            return
        if name in {"frame-shadow", "frame-opaque"}:
            try:
                await browser.snapshot()
                raise AssertionError("unverified frame was admitted")
            except RuntimeError:
                pass
            print(f"PASS compatibility {name}", flush=True)
            return
        if name.startswith("worker"):
            await browser._evaluate(
                "navigator.serviceWorker.register('/sw.js',{scope:'/'}).then(()=>navigator.serviceWorker.ready).then(()=>globalThis.workerReady=true).catch(()=>globalThis.workerRejected=true)"
            )
            if name == "worker-script-redirect":
                # Chrome closes a service-worker target whose entry script redirects.
                # Guard setup then refuses its closed CDP target and shuts down the
                # owned job; there is no live page left on which to await JS rejection.
                deadline = time.monotonic() + 12
                while not browser.policy.failed:
                    assert time.monotonic() < deadline, "redirect guard did not refuse the session"
                    await asyncio.sleep(0.05)
                assert browser._tripped and not browser.gate.armed
                await browser.close()
                assert browser.process.returncode is not None
                assert sum(path == "/sw.js" for path, _ in hits) >= 1
                assert not any(path == "/script-denied" for path, _ in asset_hits)
                try:
                    await browser.snapshot()
                    raise AssertionError("closed redirect session admitted snapshot")
                except RuntimeError:
                    pass
                print(
                    "PASS compatibility worker-script-redirect: owned job closed; destination zero",
                    flush=True,
                )
                return
            if name == "worker-script-limit":
                deadline = time.monotonic() + 8
                while not browser.policy.limited:
                    assert time.monotonic() < deadline, "worker entry script 429 was not observed"
                    await asyncio.sleep(0.05)
                print("PASS compatibility worker-script-limit", flush=True)
                return
            await wait_js(browser, "globalThis.workerReady === true")
            await wait_js(browser, "Boolean(navigator.serviceWorker.controller)")
            if name == "worker-cli-limit":
                await browser.close()
                config_path = root / "worker-cli-config.json"
                state_dir = root / "worker-cli-state"
                inbox = root / "worker-cli-inbox"
                selectors = {
                    **SELECTORS,
                    "image": ".photo",
                    "seller_name": ".seller",
                    "comment": ".comment",
                    "comment_text": ".text",
                    "comment_author": ".author",
                    "sort_indicator": "[data-sort]",
                    "pinned": "[data-pinned]",
                    "suggested": "[data-suggested]",
                }
                config_path.write_text(
                    json.dumps(
                        {
                            "synthetic": True,
                            "group_key": "owner-group-01",
                            "grant_id": "44444444-5555-4666-8777-888888888888",
                            "group_url": url.split("/groups/")[0] + "/groups/sw-429",
                            "identity_scope": "synthetic",
                            "adapter_version": "paiya-mcp-synthetic/0.1.0",
                            "grant": {
                                "active": True,
                                "scopes": ["capture", "read", "media"],
                                "retention_days": 7,
                            },
                            "post_markers_verified": True,
                            "page_size": 5,
                            "overlap": 3,
                            "scroll_budget": 0,
                            "time_budget_seconds": 30,
                            "selectors": selectors,
                        }
                    )
                )
                command = [
                    sys.executable,
                    "-m",
                    "paiya_collector.cli",
                    "discover-mcp-synthetic",
                    "--group-config",
                    str(config_path),
                    "--profile-dir",
                    str(profile),
                    "--inbox",
                    str(inbox),
                    "--state-dir",
                    str(state_dir),
                ]
                result = await asyncio.to_thread(
                    subprocess.run, command, capture_output=True, text=True, timeout=70
                )
                (root / "worker-cli-stdout.json").write_text(result.stdout)
                (root / "worker-cli-stderr.txt").write_text(result.stderr)
                report = json.loads(result.stdout)
                assert result.returncode == 2 and report["reason"] == "http_429", report
                assert not list(inbox.glob("**/READY"))
                assert (
                    load_state(state_dir / "owner-group-01.json", "owner-group-01")["checkpoint"]
                    is None
                )
                before = len(hits)
                repeated = await asyncio.to_thread(
                    subprocess.run, command, capture_output=True, text=True, timeout=20
                )
                assert repeated.returncode == 2 and len(hits) == before
                print(
                    "PASS compatibility worker-cli-limit: "
                    "no READY/checkpoint advance; restart paused",
                    flush=True,
                )
                return
            if name == "worker-response-limit":
                try:
                    await browser.navigate(url + "/sw-429")
                except RuntimeError:
                    pass
                assert browser.policy.limited, "worker-generated 429 was not observed"
                assert browser.policy.deadline is not None
                print("PASS compatibility worker-response-limit", flush=True)
                return
            if name in {"worker-script-update-limit", "worker-script-restart-limit"}:
                if name == "worker-script-restart-limit":
                    await browser._cdp("ServiceWorker.enable")
                    await browser._cdp("ServiceWorker.stopAllWorkers")
                script_limit = True
                await browser._evaluate(
                    "navigator.serviceWorker.register('/sw.js?revision=2',"
                    "{scope:'/',updateViaCache:'none'})"
                    ".then(()=>globalThis.updated=true).catch(e=>globalThis.updateError=String(e))"
                )
                deadline = time.monotonic() + 8
                while not browser.policy.limited:
                    assert time.monotonic() < deadline, (
                        "worker update script 429 was not observed",
                        browser.policy.failed,
                        await browser._evaluate(
                            "({updated:globalThis.updated,error:globalThis.updateError})"
                        ),
                        [p for p, _ in hits],
                        len(browser.worker_guard.jobs),
                        len(browser.pending),
                    )
                    await asyncio.sleep(0.05)
                print(f"PASS compatibility {name}", flush=True)
                return
            if name in {"worker-reopen", "worker-active"}:
                await browser.close()
                browser = GuardedBrowser()
                if name == "worker-active":
                    install = browser._install_guard

                    async def active_before_attach():
                        await install()
                        await browser._cdp("ServiceWorker.enable")
                        await browser._cdp(
                            "ServiceWorker.startWorker",
                            {"scopeURL": url.split("/groups/")[0] + "/"},
                        )
                        await asyncio.sleep(0.2)

                    browser._install_guard = active_before_attach
                start = len(hits)
                await browser.open(profile, group_url=url, selectors=SELECTORS, synthetic=True)
                assert not hits[start:], "source was contacted before arming"
                await browser.navigate(url)
                await wait_js(browser, "Boolean(navigator.serviceWorker.controller)")
            if name == "worker-restart":
                configure = browser.worker_guard._configure
                restart_checks = []

                async def delayed_rearm(session):
                    before_start = sum(p == "/worker-start" for p, _ in hits)
                    await asyncio.sleep(0.35)
                    assert sum(p == "/worker-start" for p, _ in hits) == before_start
                    restart_checks.append(True)
                    await configure(session)

                browser.worker_guard._configure = delayed_rearm
                await browser._cdp("ServiceWorker.enable")
                await browser._cdp("ServiceWorker.stopAllWorkers")
                await asyncio.sleep(0.2)
            message = (
                "/worker-limit"
                if name == "worker-limit"
                else "/worker-redirect"
                if name == "worker-redirect"
                else f"http://localhost:{assets.server_port}/worker-denied"
                if name == "worker-cross"
                else "idle"
                if name == "worker-idle"
                else "/worker-ok"
            )
            await browser._evaluate(
                "navigator.serviceWorker.addEventListener('message',"
                "()=>globalThis.workerDone=true); navigator.serviceWorker.controller.postMessage("
                + json.dumps(message)
                + ")"
            )
            if name == "worker-limit":
                deadline = time.monotonic() + 8
                while not browser.policy.limited:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.05)
                assert browser.policy.deadline is not None
                try:
                    await browser.snapshot()
                    raise AssertionError("429 admitted snapshot")
                except RuntimeError:
                    pass
                print("PASS compatibility worker-limit", flush=True)
                return
            await wait_js(browser, "globalThis.workerDone === true")
            if name == "worker-restart":
                for _ in range(2):
                    await browser._cdp("ServiceWorker.stopAllWorkers")
                    await browser._evaluate(
                        "globalThis.workerDone=false;"
                        "navigator.serviceWorker.controller.postMessage('/worker-ok')"
                    )
                    await wait_js(browser, "globalThis.workerDone === true")
                assert len(restart_checks) == 3
            if name == "worker-idle":
                browser.worker_guard.socket.transport.abort()
                await asyncio.sleep(2)
                assert browser.policy.failed and not browser.gate.armed
                assert not any(p == "/worker-idle-canary" for p, _ in hits)
                print("PASS compatibility worker-idle", flush=True)
                return
            if name in {"worker-cross", "worker-redirect"}:
                assert not any(p == "/worker-denied" for p, _ in asset_hits)
            assert any(p == "/worker-start" for p, _ in hits)
        elif name.startswith("cdn"):
            if name == "cdn":
                await wait_js(browser, "globalThis.moduleLoaded === true")
            else:
                assert not await browser._evaluate("globalThis.moduleLoaded === true")
            assert len(asset_hits[asset_start:]) == 1, "static resource was fetched more than once"
            assert all(
                not {key.lower() for key in headers} & {"cookie", "authorization", "referer"}
                for _, headers in asset_hits
            )
            await browser._evaluate("fetch('/verify')")
            await asyncio.sleep(0.2)
            assert all("cdn-poison" not in h.get("Cookie", "") for _, h in hits)
        result = await browser.snapshot()
        if name == "frame-challenge":
            assert result["access"] != "ok" and not result["cards_read"], result
        else:
            assert result["access"] == "ok" and result["cards_read"], result
            assert len(result["cards"]["cards"]) == 1
    finally:
        await browser.close()
    assert (profile / "sentinel").read_text() == "preserve"
    print(f"PASS compatibility {name}", flush=True)


parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", required=True, type=Path)
CASES = [
    "worker",
    "worker-reopen",
    "worker-active",
    "worker-restart",
    "worker-cross",
    "worker-redirect",
    "worker-limit",
    "worker-idle",
    "worker-script-limit",
    "worker-script-update-limit",
    "worker-script-restart-limit",
    "worker-script-redirect",
    "worker-response-limit",
    "worker-cli-limit",
    "frame",
    "frame-challenge",
    "frame-shadow",
    "frame-opaque",
    "frame-race-barrier-open",
    "frame-race-barrier-closed",
    "frame-race-evaluate-open",
    "frame-race-evaluate-closed",
    "frame-child-navigation",
    "reader-source-task",
    "reader-delayed-frame",
    "reader-unexpected-resume",
    "reader-transport-loss",
    "reader-pause-timeout",
    "reader-resume-timeout",
    "cdn",
    "cdn-denied",
    "cdn-credential",
]
parser.add_argument("--case", choices=CASES + ["all", "workers"], default="all")
args = parser.parse_args()
args.runtime_root.mkdir(parents=True, exist_ok=False)
source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
assets = ThreadingHTTPServer(("127.0.0.1", 0), Asset)
threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (source, assets)]
for thread in threads:
    thread.start()
try:
    for name in (
        CASES
        if args.case == "all"
        else [c for c in CASES if c.startswith("worker")]
        if args.case == "workers"
        else [args.case]
    ):
        asyncio.run(case(args.runtime_root, name))
finally:
    for server in (source, assets):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join()
    (args.runtime_root / "receipt.json").write_text(
        json.dumps(
            {"source_paths": [p for p, _ in hits], "asset_paths": [p for p, _ in asset_hits]},
            indent=2,
        )
    )
