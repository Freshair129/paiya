from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from paiya_collector.browser_adapter import (
    CHALLENGE_TEXT_MARKERS,
    LOGIN_TEXT_MARKERS,
    MARKER_CASEFOLD_OVERRIDES,
    LiveGroupConfig,
    PageSnapshot,
    RateLimitPause,
    VisibleComment,
    VisiblePost,
    _matches_group_path,
    _raise_for_rate_limit,
    classify_page,
    parse_retry_after,
)

# Scope and text extraction share one synchronous evaluation, so a history
# transition cannot interleave with a read of live locators.
_READ_BODY = """({url, selectors}) => {
    if (!(location.href === url) || !document.body) return null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const parts = [];
    let length = 0;
    let nodes = 0;
    while (walker.nextNode()) {
        if (++nodes > 50000) return {truncated: true};
        const node = walker.currentNode;
        const parent = node.parentElement;
        if (!parent || parent.closest('script,style,noscript,template')) continue;
        const card = selectors.post_card && parent.closest(selectors.post_card);
        const dialog = parent.closest('[role="dialog"],[role="alertdialog"],[aria-modal="true"]');
        const alert = parent.closest('[role="alert"],[aria-live="assertive"],form');
        const securityUi = dialog || (alert && card && card.contains(alert));
        const content = [selectors.post_text, selectors.comment]
            .filter(Boolean).map(selector => parent.closest(selector))
            .some(element => element && card && element !== card && card.contains(element));
        if (content && !securityUi) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        if (!range.getClientRects().length ||
            getComputedStyle(parent).visibility !== 'visible') continue;
        const text = node.nodeValue || "";
        if (length + text.length > 20000) return {truncated: true};
        parts.push(text);
        length += text.length;
    }
    return {truncated: false, text: parts.join("")};
}"""

_READ_CARDS = """({url, selectors}) => {
    if (location.href !== url) return null;
    const one = (owner, selector) => selector ? owner.querySelector(selector) : null;
    const all = (owner, selector) => selector ? Array.from(owner.querySelectorAll(selector)) : [];
    const marked = (card, selector) => selector && (card.matches(selector) || one(card, selector));
    const text = (owner, selector) => one(owner, selector)?.innerText?.trim() || "";
    const cards = all(document, selectors.post_card);
    if (cards.length > 200) return {tooMany: true};
    return {
        sortLabel: text(document, selectors.sort_indicator),
        feedEnd: Boolean(one(document, selectors.feed_end)),
        cards: cards.map(card => ({
            postText: text(card, selectors.post_text),
            href: one(card, selectors.permalink)?.getAttribute("href") ?? null,
            sellerName: text(card, selectors.seller_name),
            pinned: Boolean(marked(card, selectors.pinned)),
            suggested: Boolean(marked(card, selectors.suggested)),
            comments: all(card, selectors.comment).slice(0, 50).map(comment => {
                const time = one(comment, "time");
                return {
                    text: text(comment, selectors.comment_text),
                    author: text(comment, selectors.comment_author),
                    key: comment.getAttribute("data-commentid")
                        || comment.getAttribute("data-comment-key"),
                    time: time?.getAttribute("aria-label") || time?.getAttribute("datetime")
                        || time?.innerText?.trim() || null
                };
            }).filter(comment => comment.text)
        }))
    };
}"""

# Do not yield to page scripts between the security scan and card extraction.
_READ_SNAPSHOT = f"""args => {{
    const body = ({_READ_BODY})(args);
    if (!body || body.truncated) return {{body, cards: null}};
    const markers = {json.dumps(CHALLENGE_TEXT_MARKERS + LOGIN_TEXT_MARKERS, ensure_ascii=False)};
    const overrides = {json.dumps(MARKER_CASEFOLD_OVERRIDES, ensure_ascii=True)};
    const text = Array.from(body.text, char => overrides[char] ??
        (/^[A-Z]$/.test(char) ? char.toLowerCase() : char)).join('');
    if (markers.some(marker => text.includes(marker)))
        return {{body, cards: null}};
    return {{body, cards: ({_READ_CARDS})(args)}};
}}"""


class BrowserAdapterError(RuntimeError):
    pass


def read_devtools_endpoint(
    profile_dir: Path,
    *,
    group_key: str,
    repository_root: Path,
) -> str:
    profile = profile_dir.resolve()
    repo = repository_root.resolve()
    if profile.is_relative_to(repo):
        raise ValueError("dedicated browser profile must be stored outside the repository")
    if profile.drive.casefold() != repo.drive.casefold():
        raise ValueError("dedicated browser profile must be stored on the Collector volume")

    local_app_data = Path.home() / "AppData" / "Local"
    if local_app_data.exists():
        default_profiles = (
            local_app_data / "Google" / "Chrome" / "User Data",
            local_app_data / "Microsoft" / "Edge" / "User Data",
        )
        for default_profile in default_profiles:
            resolved_default = default_profile.resolve()
            if profile == resolved_default or profile.is_relative_to(resolved_default):
                raise ValueError("everyday browser profiles are not allowed")

    marker = profile / "PAIYA_COLLECTOR_PROFILE"
    active_port = profile / "DevToolsActivePort"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("dedicated browser profile marker is missing")
    if marker.read_text(encoding="utf-8").strip() != group_key:
        raise ValueError("dedicated browser profile does not match the configured group")
    if active_port.is_symlink() or not active_port.is_file():
        raise ValueError("dedicated Chrome must be opened with open-dedicated-chrome.ps1")

    lines = active_port.read_text(encoding="ascii").splitlines()
    if len(lines) < 2:
        raise ValueError("DevToolsActivePort is malformed")
    try:
        port = int(lines[0], 10)
    except ValueError as exc:
        raise ValueError("DevToolsActivePort contains an invalid port") from exc
    if not 1 <= port <= 65535 or not lines[1].startswith("/devtools/browser/"):
        raise ValueError("DevToolsActivePort contains an invalid endpoint")
    return f"http://127.0.0.1:{port}"


def _http_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


class PlaywrightVisiblePage:
    def __init__(self, page: object, group_url: str) -> None:
        origin = _http_origin(group_url)
        if origin is None:
            raise BrowserAdapterError("configured group URL has no valid origin")
        self._page = page
        self._group_url = group_url
        self._origin = origin
        self._session = None
        self._main_frame: str | None = None
        self._blocked_reason: str | None = None
        self._document_revision = 0
        self._http_status: int | None = None
        self._retry_after: str | None = None
        self._response_received_at: datetime | None = None
        page.on("response", self._on_response)

    def _guard_navigation(self) -> None:
        if self._session is not None:
            return
        session = self._page.context.new_cdp_session(self._page)
        self._session = session
        session.send("Page.enable")
        tree = session.send("Page.getFrameTree")
        self._main_frame = tree["frameTree"]["frame"]["id"]
        session.send("Network.setBypassServiceWorker", {"bypass": True})
        session.on("Fetch.requestPaused", self._on_document_request)
        session.on("Page.frameNavigated", self._on_frame_navigated)
        session.on("Page.navigatedWithinDocument", self._on_same_document_navigation)
        session.send("Fetch.enable", {"patterns": [{"resourceType": "Document"}]})

    def _on_document_request(self, event: dict) -> None:
        session = self._session
        if session is None:
            return
        request = {"requestId": event["requestId"]}
        url = event["request"]["url"]
        main_frame = event.get("frameId") == self._main_frame
        if _http_origin(url) != self._origin:
            reason = "navigation outside configured origin was blocked"
        elif main_frame and not self._allowed_group_document(url):
            reason = "navigation outside configured group was blocked"
        else:
            session.send("Fetch.continueRequest", request)
            return
        if main_frame:
            self._blocked_reason = reason
        session.send("Fetch.failRequest", {**request, "errorReason": "BlockedByClient"})

    def _on_frame_navigated(self, event: dict) -> None:
        if event["frame"]["id"] == self._main_frame:
            self._document_revision += 1

    def _on_same_document_navigation(self, event: dict) -> None:
        if event["frameId"] == self._main_frame:
            self._document_revision += 1

    def _allowed_group_document(self, url: str) -> bool:
        path = urlsplit(url).path.casefold().strip("/").split("/", 1)[0]
        return path in {"login", "checkpoint", "challenge", "security"} or _matches_group_path(
            url, self._group_url
        )

    def _check_origin(self, url: str) -> None:
        if self._blocked_reason:
            raise BrowserAdapterError(self._blocked_reason)
        if _http_origin(url) != self._origin:
            raise BrowserAdapterError("navigation outside configured origin was blocked")

    def _check_snapshot(self, url: str, revision: int) -> None:
        current = str(self._page.url)
        self._check_origin(current)
        if url != current or revision != self._document_revision:
            raise BrowserAdapterError("visible browser document changed during capture")

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.send("Fetch.disable")
                self._session.send("Network.setBypassServiceWorker", {"bypass": False})
            finally:
                self._session.detach()
                self._session = None

    def raise_if_rate_limited(self) -> None:
        if self._http_status == 429:
            _raise_for_rate_limit(
                PageSnapshot(
                    self._group_url,
                    "",
                    "",
                    True,
                    (),
                    self._retry_after,
                    self._http_status,
                    self._response_received_at,
                ),
                now=datetime.now(UTC),
            )

    def navigate(self, url: str) -> None:
        try:
            self._check_origin(url)
            if not self._allowed_group_document(url):
                raise BrowserAdapterError("navigation outside configured group was blocked")
            self._guard_navigation()
            current = str(self._page.url)
            current_parts = urlsplit(current)
            if (
                _http_origin(current) == self._origin
                and current_parts.path.rstrip("/") == urlsplit(url).path.rstrip("/")
                and current_parts.query == "sorting_setting=CHRONOLOGICAL"
                and not current_parts.fragment
            ):
                url = current
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self._check_origin(str(self._page.url))
        except BrowserAdapterError:
            raise
        except Exception as exc:
            if self._blocked_reason:
                raise BrowserAdapterError(self._blocked_reason) from None
            if self._http_status == 429:
                return
            raise BrowserAdapterError(
                f"visible browser navigation failed ({type(exc).__name__}); source URL omitted"
            ) from None

    def snapshot(self, selectors: dict[str, str]) -> PageSnapshot:
        try:
            url = str(self._page.url)
            revision = self._document_revision
            if self._http_status == 429 and not self._blocked_reason:
                return PageSnapshot(
                    self._group_url,
                    "",
                    "",
                    True,
                    (),
                    self._retry_after,
                    self._http_status,
                    self._response_received_at,
                )
            self._check_origin(url)
            if classify_page(url, "") != "ok":
                return PageSnapshot(url, "", "", True, ())
            if not _matches_group_path(url, self._group_url):
                raise BrowserAdapterError("visible browser left the configured group")
            read = self._page.evaluate(_READ_SNAPSHOT, {"url": url, "selectors": selectors})
            self._check_snapshot(url, revision)
            if read is None or read.get("body") is None:
                raise BrowserAdapterError("visible browser document changed during capture")
            body_scan = read["body"]
            if body_scan.get("truncated"):
                raise BrowserAdapterError("visible page security scan exceeded its safe limit")
            body_text = body_scan["text"]
            if classify_page(url, body_text) != "ok":
                return PageSnapshot(url, body_text, "", True, ())
            snapshot = read.get("cards")
            if snapshot is None:
                raise BrowserAdapterError("visible browser document changed during capture")
            if snapshot.get("tooMany"):
                raise BrowserAdapterError("visible card count exceeded the 200-card read bound")

            posts = []
            for index, card in enumerate(snapshot["cards"]):
                self._check_snapshot(url, revision)
                posts.append(self._read_post(card, selectors, url, index))
            self._check_snapshot(url, revision)
            return PageSnapshot(
                url=url,
                body_text=body_text,
                sort_label=snapshot["sortLabel"],
                feed_end=snapshot["feedEnd"],
                posts=tuple(posts),
                retry_after=self._retry_after,
                http_status=self._http_status,
                response_received_at=self._response_received_at,
            )
        except BrowserAdapterError:
            raise
        except Exception as exc:
            raise BrowserAdapterError(
                f"visible browser extraction failed ({type(exc).__name__}); source URL omitted"
            ) from None

    def scroll_one_step(self) -> None:
        try:
            url = str(self._page.url)
            self._check_origin(url)
            if not _matches_group_path(url, self._group_url):
                raise BrowserAdapterError("visible browser left the configured group")
            self._page.evaluate("window.scrollBy(0, Math.max(640, window.innerHeight * 0.8))")
            self._page.wait_for_timeout(500)
        except BrowserAdapterError:
            raise
        except Exception as exc:
            raise BrowserAdapterError(
                f"visible browser scroll failed ({type(exc).__name__}); source URL omitted"
            ) from None

    def _read_post(
        self,
        card: dict,
        selectors: dict[str, str],
        page_url: str,
        index: int,
    ) -> VisiblePost:
        image = selectors.get("image")
        image_bytes, image_capture_failed = (None, False)
        if image:
            card_locator = self._page.locator(selectors["post_card"]).nth(index)
            image_bytes, image_capture_failed = self._capture_image(
                card_locator, image, page_url, selectors["permalink"], card["href"]
            )
        return VisiblePost(
            permalink=urljoin(page_url, card["href"] or ""),
            post_text=card["postText"],
            seller_name=card["sellerName"] or None,
            comments=tuple(
                VisibleComment(
                    text=comment["text"],
                    author_name=comment["author"] or None,
                    comment_key=comment["key"],
                    source_time=comment["time"],
                )
                for comment in card["comments"]
            ),
            image_bytes=image_bytes,
            image_capture_failed=image_capture_failed,
            pinned=card["pinned"],
            suggested=card["suggested"],
            scroll_step=index,
            reported_comment_count=None,
        )

    @staticmethod
    def _capture_image(
        card: object,
        selector: str,
        page_url: str,
        permalink_selector: str,
        expected_href: str | None,
    ) -> tuple[bytes | None, bool]:
        card_handle = None
        image_handle = None
        try:
            if not card.count():
                return None, True
            card_handle = card.element_handle(timeout=250)
            if not card_handle or not expected_href:
                return None, True
            check = """(card, expected) => card.isConnected
                && card.ownerDocument.URL === expected.url
                && card.querySelector(expected.selector)?.getAttribute('href') === expected.href"""
            expected = {"url": page_url, "selector": permalink_selector, "href": expected_href}
            if not card_handle.evaluate(check, expected):
                return None, True
            image_handle = card_handle.query_selector(selector)
            if not image_handle:
                return None, not card_handle.evaluate(check, expected)
            if not image_handle.is_visible() or not card_handle.evaluate(
                "(card, image) => card.contains(image)", image_handle
            ):
                return None, True
            captured = image_handle.screenshot(type="jpeg", quality=80, timeout=3_000)
            if not card_handle.evaluate(check, expected) or not card_handle.evaluate(
                "(card, image) => card.contains(image)", image_handle
            ):
                return None, True
            return (captured, False) if captured else (None, True)
        except Exception:
            return None, True
        finally:
            if image_handle is not None:
                image_handle.dispose()
            if card_handle is not None:
                card_handle.dispose()

    def _on_response(self, response: object) -> None:
        if getattr(response, "status", None) != 429:
            return
        received_at = datetime.now(UTC)
        try:
            headers = response.headers
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:
            retry_after = None
        new_deadline = parse_retry_after(retry_after, now=received_at)
        if self._http_status == 429 and self._response_received_at is not None:
            previous_deadline = parse_retry_after(self._retry_after, now=self._response_received_at)
            if previous_deadline is None or (
                new_deadline is not None and previous_deadline >= new_deadline
            ):
                return
        self._http_status = 429
        self._response_received_at = received_at
        self._retry_after = retry_after


@contextmanager
def attach_dedicated_browser(
    profile_dir: Path,
    *,
    config: LiveGroupConfig,
    repository_root: Path,
) -> Iterator[PlaywrightVisiblePage]:
    endpoint = read_devtools_endpoint(
        profile_dir,
        group_key=config.group_key,
        repository_root=repository_root,
    )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserAdapterError(
            "Playwright is not installed; run scripts/setup-browser.ps1 first"
        ) from exc

    runtime = sync_playwright().start()
    browser = None
    visible_page = None
    try:
        browser = runtime.chromium.connect_over_cdp(endpoint, timeout=5_000)
        contexts = browser.contexts
        if not contexts:
            raise BrowserAdapterError("dedicated Chrome has no visible browser context")
        pages = contexts[0].pages
        if not pages:
            raise BrowserAdapterError("dedicated Chrome has no visible tab; reopen its profile")
        page = pages[0]
        visible_page = PlaywrightVisiblePage(page, config.group_url)
        yield visible_page
    except (BrowserAdapterError, RateLimitPause):
        raise
    except Exception as exc:
        raise BrowserAdapterError(
            f"dedicated Chrome connection failed ({type(exc).__name__}); source URL omitted"
        ) from None
    finally:
        if visible_page is not None:
            try:
                visible_page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        runtime.stop()
