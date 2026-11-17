import asyncio
import base64
import gzip
import threading
import zlib
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from paiya_collector.browser_adapter import RateLimitPause
from paiya_collector.browser_runtime import BrowserAdapterError
from paiya_collector.mcp_assets import fetch_asset
from paiya_collector.mcp_policy import RequestPolicy


@pytest.fixture
def assets():
    hits = []
    servers = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.server.server_port, self.path, dict(self.headers)))
            if self.path == "/credential-required":
                self.send_response(401)
                self.send_header("Set-Cookie", "login=required; SameSite=None; Secure")
                self.end_headers()
                return
            if self.path in {"/302", "/307", "/denied", "/loop"}:
                self.send_response(307 if self.path == "/307" else 302)
                self.send_header("Set-Cookie", "asset_canary=secret; Path=/")
                target = servers[2] if self.path == "/denied" else servers[1]
                location = (
                    f"http://localhost:{self.server.server_port}/loop"
                    if self.path == "/loop"
                    else f"http://localhost:{target.server_port}/image"
                )
                self.send_header("Location", location)
                self.end_headers()
                return
            if self.path in {"/limit", "/unknown"}:
                self.send_response(429)
                if self.path == "/limit":
                    self.send_header("Retry-After", "120")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/html" if self.path == "/html" else "image/svg+xml"
            )
            self.send_header("Set-Cookie", "asset_final=secret; Path=/")
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "null"))
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            if self.path in {"/gzip", "/deflate", "/bomb", "/concatenated"}:
                self.send_header(
                    "Content-Encoding", "deflate" if self.path == "/deflate" else "gzip"
                )
            if self.path == "/compressed":
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            try:
                if self.path in {"/gzip", "/deflate", "/bomb", "/concatenated"}:
                    plain = b"x" * (9 * 1024 * 1024) if self.path == "/bomb" else b"fixture"
                    body = zlib.compress(plain) if self.path == "/deflate" else gzip.compress(plain)
                    self.wfile.write(body * (2 if self.path == "/concatenated" else 1))
                    return
                self.wfile.write(
                    b"x" * (9 * 1024 * 1024)
                    if self.path == "/huge"
                    else b'<svg xmlns="http://www.w3.org/2000/svg"/>'
                )
            except (ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, *_):
            pass

    servers[:] = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(3)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    try:
        origins = [f"http://localhost:{server.server_port}" for server in servers]
        yield origins, hits
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


def policy(origins):
    return RequestPolicy(
        "http://localhost:1/groups/demo", synthetic=True, asset_origins=origins[:2]
    )


@pytest.mark.parametrize("code", [302, 307])
def test_asset_redirect_never_forwards_or_persists_cookie(assets, code):
    origins, hits = assets
    result = asyncio.run(
        fetch_asset(f"{origins[0]}/{code}", policy=policy(origins), resource_type="Image")
    )
    assert len(hits) == 2
    for _, _, headers in hits:
        assert not {key.lower() for key in headers} & {
            "cookie",
            "authorization",
            "proxy-authorization",
            "referer",
            "x-csrf-token",
        }
    assert not {header["name"].lower() for header in result["responseHeaders"]} & {"set-cookie"}
    assert base64.b64decode(result["body"]).startswith(b"<svg")


def test_asset_denies_redirect_before_unlisted_origin(assets):
    origins, hits = assets
    with pytest.raises(BrowserAdapterError, match="denied"):
        asyncio.run(
            fetch_asset(origins[0] + "/denied", policy=policy(origins), resource_type="Image")
        )
    assert len(hits) == 1


@pytest.mark.parametrize(
    "path, message",
    [
        ("/loop", "denied"),
        ("/huge", "byte budget"),
        ("/html", "MIME"),
        ("/compressed", "compressed"),
        ("/bomb", "decoded budget"),
        ("/concatenated", "decoded budget"),
    ],
)
def test_asset_limits(assets, path, message):
    origins, _ = assets
    with pytest.raises(BrowserAdapterError, match=message):
        asyncio.run(fetch_asset(origins[0] + path, policy=policy(origins), resource_type="Image"))


@pytest.mark.parametrize("path, manual", [("/limit", False), ("/unknown", True)])
def test_asset_429_latches_receipt_deadline(assets, path, manual):
    origins, _ = assets
    guard = policy(origins)
    before = datetime.now(UTC)
    with pytest.raises(RateLimitPause) as caught:
        asyncio.run(fetch_asset(origins[0] + path, policy=guard, resource_type="Image"))
    assert caught.value.manual_resume_required is manual
    if not manual:
        assert 119 <= (caught.value.retry_after_until - before).total_seconds() <= 123
    with pytest.raises(RateLimitPause):
        guard.check()


def test_asset_allowlist_never_allows_a_document_or_xhr(assets):
    origins, _ = assets
    guard = policy(origins)
    assert not guard.allow(origins[0] + "/data", document=False, resource_type="XHR")
    assert not guard.allow(origins[0] + "/page", document=True, resource_type="Document")


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_asset_decodes_bounded_body_and_preserves_cors_policy(assets, encoding):
    origins, hits = assets
    result = asyncio.run(
        fetch_asset(origins[0] + "/" + encoding, policy=policy(origins), resource_type="Image")
    )
    assert base64.b64decode(result["body"]) == b"fixture"
    headers = {entry["name"].lower(): entry["value"] for entry in result["responseHeaders"]}
    assert headers["access-control-allow-origin"] == "http://localhost:1"
    assert headers["access-control-allow-credentials"] == "true"
    assert headers["cross-origin-resource-policy"] == "cross-origin"
    assert headers["cache-control"] == "no-store"
    assert "set-cookie" not in headers and "content-encoding" not in headers
    assert hits[0][2]["Origin"] == "http://localhost:1"
    assert "Cookie" not in hits[0][2]


def test_credential_dependent_cdn_fails_without_cookie_retry(assets):
    origins, hits = assets
    with pytest.raises(BrowserAdapterError, match="unavailable"):
        asyncio.run(
            fetch_asset(
                origins[0] + "/credential-required", policy=policy(origins), resource_type="Image"
            )
        )
    assert len(hits) == 1
    assert not {key.lower() for key in hits[0][2]} & {"cookie", "authorization", "referer"}
