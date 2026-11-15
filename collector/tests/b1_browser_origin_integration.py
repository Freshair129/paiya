"""B1 browser acceptance against two loopback origins and invented credentials."""

import os
from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from paiya_collector.browser_runtime import BrowserAdapterError, PlaywrightVisiblePage

CANARY = "synthetic-b1-credential"


@contextmanager
def serve(reply) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = reply(self.path, self.headers)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        chrome = runtime.chromium.launch(
            executable_path=os.environ.get("PAIYA_B1_CHROME"),
            args=["--disable-background-networking", "--no-first-run"],
        )
        try:
            yield chrome
        finally:
            chrome.close()


@pytest.mark.parametrize("entry", ["link", "302", "307-chain"])
@pytest.mark.parametrize("credential", ["authorization", "cookie", "http-auth"])
def test_browser_blocks_cross_origin_documents_before_credentials_can_arrive(
    browser, entry, credential
) -> None:
    from playwright.sync_api import Error

    received = []
    source_credential = []
    basic = "Basic " + b64encode(f"b1-user:{CANARY}".encode()).decode()
    header, exact = {
        "authorization": ("Authorization", f"Bearer {CANARY}"),
        "cookie": ("Cookie", f"b1_canary={CANARY}"),
        "http-auth": ("Authorization", basic),
    }[credential]

    def destination(path, headers):
        received.append((path, headers.get("Cookie"), headers.get("Authorization")))
        return 200, {}, b"<body>Foreign document</body>"

    with serve(destination) as receiver:

        def source(path, headers):
            if path.startswith("/groups/"):
                value = headers.get(header)
                if credential == "http-auth" and value != exact:
                    return 401, {"WWW-Authenticate": 'Basic realm="Synthetic B1"'}, b""
                source_credential.append(value)
            if path == "/groups/b1/302":
                return 302, {"Location": f"{receiver}/sink"}, b""
            if path == "/groups/b1/307-chain":
                return 307, {"Location": "/groups/b1/302"}, b""
            if path == "/groups/b1/control":
                return 302, {"Location": "/groups/b1/link"}, b""
            if path == "/groups/b1/link":
                return (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    (
                        '<body><span id="sort">newest posts</span>'
                        "<article><p>RTX 3060 12GB ราคา 5,500 บาท</p>"
                        '<a href="/groups/b1/posts/99">post</a>'
                        '<span class="seller">Synthetic</span>'
                        '<div class="comment" data-comment-key="c-01">'
                        '<time datetime="5 นาที"></time>'
                        '<span class="text">สนใจครับ</span><span class="author">Buyer</span></div>'
                        '<svg class="photo" width="20" height="20">'
                        '<rect width="20" height="20" fill="orange"/></svg></article>'
                        f'<a id="foreign" href="{receiver}/sink">link</a></body>'
                    ).encode(),
                )
            return 404, {}, b""

        with serve(source) as owner:
            options = (
                {"http_credentials": {"username": "b1-user", "password": CANARY, "origin": owner}}
                if credential == "http-auth"
                else {"extra_http_headers": {header: exact}}
            )
            context = browser.new_context(**options)
            try:
                page = context.new_page()
                guarded = PlaywrightVisiblePage(page, f"{owner}/groups/b1")
                try:
                    guarded.navigate(f"{owner}/groups/b1/control")
                    assert page.url == f"{owner}/groups/b1/link"
                    assert exact in source_credential
                    snapshot = guarded.snapshot(
                        {
                            "sort_indicator": "#sort",
                            "post_card": "article",
                            "post_text": "p",
                            "permalink": "a",
                            "seller_name": ".seller",
                            "comment": ".comment",
                            "comment_text": ".text",
                            "comment_author": ".author",
                            "image": ".photo",
                        }
                    )
                    assert snapshot.sort_label == "newest posts"
                    (post,) = snapshot.posts
                    assert post.permalink == f"{owner}/groups/b1/posts/99"
                    assert "5,500 บาท" in post.post_text
                    assert post.seller_name == "Synthetic"
                    assert post.comments[0].comment_key == "c-01"
                    assert post.image_bytes and post.image_bytes.startswith(b"\xff\xd8\xff")
                    if entry == "link":
                        try:
                            page.locator("#foreign").click(timeout=3_000)
                        except Error as error:
                            assert "ERR_BLOCKED_BY_CLIENT" in str(error)
                    else:
                        with pytest.raises(BrowserAdapterError, match="outside configured origin"):
                            guarded.navigate(f"{owner}/groups/b1/{entry}")
                    assert received == []
                    with pytest.raises(BrowserAdapterError, match="outside configured origin"):
                        guarded.snapshot({})
                finally:
                    guarded.close()
            finally:
                context.close()
