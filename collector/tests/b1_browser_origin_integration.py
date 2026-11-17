"""B1 browser acceptance against two loopback origins and invented credentials."""

import os
from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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


def test_preserved_group_feed_still_blocks_cross_origin_navigation(browser) -> None:
    from playwright.sync_api import Error

    received = []

    def destination(path, headers):
        received.append(path)
        return 200, {}, b"<body>Foreign document</body>"

    with serve(destination) as receiver:

        def source(path, headers):
            body = f'<body><a id="foreign" href="{receiver}/sink">link</a></body>'
            return 200, {"Content-Type": "text/html"}, body.encode()

        with serve(source) as owner:
            context = browser.new_context()
            try:
                page = context.new_page()
                selected_url = f"{owner}/groups/b1?sorting_setting=CHRONOLOGICAL"
                page.goto(selected_url)
                guarded = PlaywrightVisiblePage(page, f"{owner}/groups/b1")
                try:
                    guarded.navigate(f"{owner}/groups/b1")
                    assert page.url == selected_url
                    try:
                        page.locator("#foreign").click(timeout=3_000)
                    except Error as error:
                        assert "ERR_BLOCKED_BY_CLIENT" in str(error)
                    assert received == []
                    with pytest.raises(BrowserAdapterError, match="outside configured origin"):
                        guarded.snapshot({})
                finally:
                    guarded.close()
            finally:
                context.close()


def test_preserved_sort_navigation_observes_rate_limit(browser) -> None:
    calls = []

    def source(path, headers):
        if not path.startswith("/groups/b1"):
            return 404, {}, b""
        calls.append(path)
        if len(calls) == 1:
            return 200, {"Content-Type": "text/html"}, b"<body>Initial feed</body>"
        return 429, {"Retry-After": "120"}, b"<body>Rate limited</body>"

    with serve(source) as owner:
        context = browser.new_context()
        try:
            page = context.new_page()
            selected_url = f"{owner}/groups/b1?sorting_setting=CHRONOLOGICAL"
            page.goto(selected_url)
            guarded = PlaywrightVisiblePage(page, f"{owner}/groups/b1")
            try:
                before_429 = datetime.now(UTC)
                guarded.navigate(f"{owner}/groups/b1")
                snapshot = guarded.snapshot({})
                after_429 = datetime.now(UTC)
                assert len(calls) == 2
                assert snapshot.http_status == 429
                assert snapshot.retry_after == "120"
                assert snapshot.response_received_at is not None
                assert before_429 <= snapshot.response_received_at <= after_429
                assert snapshot.posts == ()
            finally:
                guarded.close()
        finally:
            context.close()


@pytest.mark.parametrize(
    ("post_text", "comment", "dialog", "outside", "expected"),
    [
        ("สินค้าพร้อมยืนยันตัวตน", "", "", "", "ok"),
        ("สินค้าทดสอบ", "ยืนยันตัวตนก่อนโอน", "", "", "ok"),
        ("สินค้าทดสอบ", "", '<div role="dialog">ยืนยันตัวตน</div>', "", "challenge_required"),
        ('<div role="dialog">ยืนยันตัวตน</div>', "", "", "", "challenge_required"),
        ("สินค้าทดสอบ", "", "", "ยืน<span>ยันตัวตน</span>", "challenge_required"),
        ("ยืนยันตัวตน", "", "", "ยืนยันตัวตน", "challenge_required"),
        (
            "สินค้าทดสอบ",
            "",
            "",
            '<span style="display:contents">ยืนยันตัวตน</span>',
            "challenge_required",
        ),
    ],
)
def test_challenge_checks_exclude_only_post_and_comment_text(
    browser, post_text, comment, dialog, outside, expected
) -> None:
    from paiya_collector.browser_adapter import classify_page

    def source(path, headers):
        body = (
            f'<body><form><article><div class="message">{post_text}</div>'
            f'<div class="comment"><span class="text">{comment}</span></div>{dialog}'
            '<a href="/groups/b1/posts/99">post</a><span class="seller">Synthetic</span>'
            f'</article></form><div id="outside">{outside}</div></body>'
        )
        return 200, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")

    with serve(source) as owner:
        context = browser.new_context()
        try:
            guarded = PlaywrightVisiblePage(context.new_page(), f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                snap = guarded.snapshot(
                    {
                        "post_card": "article",
                        "post_text": ".message",
                        "comment": ".comment",
                        "comment_text": ".text",
                        "comment_author": ".author",
                        "permalink": "a",
                        "seller_name": ".seller",
                    }
                )
                assert classify_page(snap.url, snap.body_text) == expected
                assert len(snap.posts) == (1 if expected == "ok" else 0)
            finally:
                guarded.close()
        finally:
            context.close()


def test_security_scan_and_card_snapshot_are_atomic(browser) -> None:
    def source(path, headers):
        body = (
            b'<body><article><div class="message">Synthetic</div>'
            b'<a href="/groups/b1/posts/1">Post</a><span class="seller">Seller</span>'
            b"</article></body>"
        )
        return 200, {"Content-Type": "text/html"}, body

    with serve(source) as owner:
        context = browser.new_context()
        try:
            actual_page = context.new_page()
            evaluations = []

            class PageProxy:
                def __getattr__(self, name):
                    return getattr(actual_page, name)

                def evaluate(self, script, arg):
                    evaluations.append(script)
                    return actual_page.evaluate(script, arg)

            guarded = PlaywrightVisiblePage(actual_page, f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                guarded._page = PageProxy()
                snap = guarded.snapshot(
                    {
                        "post_card": "article",
                        "post_text": ".message",
                        "comment": ".comment",
                        "comment_text": ".text",
                        "comment_author": ".author",
                        "permalink": "a",
                        "seller_name": ".seller",
                    }
                )
                assert len(snap.posts) == 1
                assert len(evaluations) == 1
            finally:
                guarded.close()
        finally:
            context.close()


@pytest.mark.parametrize("warning", ["ſecurity check", "ßecurity check", "SECURITY CHECK"])
def test_unicode_challenge_never_reads_card_text(browser, warning) -> None:
    def source(path, headers):
        body = f"""<body><div>{warning}</div><article>
          <div class="message">Synthetic post</div><a href="/groups/b1/posts/1">Post</a>
          <span class="seller">Seller</span></article>
          <script>
            window.cardRead = false;
            const query = Element.prototype.querySelector;
            Element.prototype.querySelector = function(selector) {{
              if (selector === '.message') window.cardRead = true;
              return query.call(this, selector);
            }};
          </script></body>"""
        return 200, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")

    with serve(source) as owner:
        context = browser.new_context()
        try:
            page = context.new_page()
            guarded = PlaywrightVisiblePage(page, f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                snap = guarded.snapshot(
                    {
                        "post_card": "article",
                        "post_text": ".message",
                        "comment": ".comment",
                        "comment_text": ".text",
                        "comment_author": ".author",
                        "permalink": "a",
                        "seller_name": ".seller",
                    }
                )
                assert not snap.posts
                assert page.evaluate("window.cardRead") is False
            finally:
                guarded.close()
        finally:
            context.close()


def test_long_page_security_scan_stops_before_extraction(browser) -> None:
    def source(path, headers):
        body = (
            "<body><div>" + ("X" * 20001) + '</div><div role="dialog">ยืนยันตัวตน</div>'
            '<article><div class="message">Synthetic post</div></article></body>'
        )
        return 200, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")

    with serve(source) as owner:
        context = browser.new_context()
        try:
            guarded = PlaywrightVisiblePage(context.new_page(), f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                with pytest.raises(BrowserAdapterError, match="security scan"):
                    guarded.snapshot({"post_card": "article", "post_text": ".message"})
            finally:
                guarded.close()
        finally:
            context.close()


def test_many_security_scan_nodes_stop_before_extraction(browser) -> None:
    def source(path, headers):
        body = (
            '<body><article><div class="message">'
            + ("<span>x</span>" * 50001)
            + "</div></article></body>"
        )
        return 200, {"Content-Type": "text/html"}, body.encode("utf-8")

    with serve(source) as owner:
        context = browser.new_context()
        try:
            guarded = PlaywrightVisiblePage(context.new_page(), f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                with pytest.raises(BrowserAdapterError, match="security scan"):
                    guarded.snapshot({"post_card": "article", "post_text": ".message"})
            finally:
                guarded.close()
        finally:
            context.close()


def test_reordered_feed_never_attaches_another_posts_image(browser) -> None:
    def source(path, headers):
        body = (
            '<body><article><a href="/groups/b1/posts/1">first</a>'
            '<svg class="photo" width="20" height="20"><rect width="20" height="20"/></svg>'
            '</article><article><a href="/groups/b1/posts/2">second</a>'
            '<svg class="photo" width="20" height="20"><rect width="20" height="20"/></svg>'
            "</article></body>"
        )
        return 200, {"Content-Type": "text/html"}, body.encode()

    with serve(source) as owner:
        context = browser.new_context()
        try:
            page = context.new_page()
            guarded = PlaywrightVisiblePage(page, f"{owner}/groups/b1")
            try:
                guarded.navigate(f"{owner}/groups/b1")
                first_card = {
                    "href": "/groups/b1/posts/1",
                    "postText": "Synthetic first post",
                    "sellerName": "Synthetic seller",
                    "comments": [],
                    "pinned": False,
                    "suggested": False,
                }
                page.locator("article").first.evaluate(
                    "first => first.parentNode.appendChild(first)"
                )
                post = guarded._read_post(
                    first_card,
                    {"post_card": "article", "image": ".photo", "permalink": "a"},
                    page.url,
                    0,
                )
                assert post.image_bytes is None
                assert post.image_capture_failed
            finally:
                guarded.close()
        finally:
            context.close()
