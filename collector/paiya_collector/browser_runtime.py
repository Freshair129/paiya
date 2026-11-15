from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from paiya_collector.browser_adapter import (
    LiveGroupConfig,
    PageSnapshot,
    VisibleComment,
    VisiblePost,
    _matches_group_path,
    classify_page,
)

# Scope and text extraction share one synchronous evaluation, so a history
# transition cannot interleave with a read of live locators.
_READ_BODY = """url => location.href === url && document.body
    ? document.body.innerText.slice(0, 20000) : null"""

_READ_CARDS = """({url, selectors}) => {
    if (location.href !== url) return null;
    const one = (owner, selector) => selector ? owner.querySelector(selector) : null;
    const all = (owner, selector) => selector ? Array.from(owner.querySelectorAll(selector)) : [];
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
            pinned: Boolean(one(card, selectors.pinned)),
            suggested: Boolean(one(card, selectors.suggested)),
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

    def navigate(self, url: str) -> None:
        try:
            self._check_origin(url)
            if not self._allowed_group_document(url):
                raise BrowserAdapterError("navigation outside configured group was blocked")
            self._guard_navigation()
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
                    self._group_url, "", "", True, (), self._retry_after, self._http_status
                )
            self._check_origin(url)
            if classify_page(url, "") != "ok":
                return PageSnapshot(url, "", "", True, ())
            if not _matches_group_path(url, self._group_url):
                raise BrowserAdapterError("visible browser left the configured group")
            body_text = self._page.evaluate(_READ_BODY, url)
            self._check_snapshot(url, revision)
            if body_text is None:
                raise BrowserAdapterError("visible browser document changed during capture")
            if classify_page(url, body_text) != "ok":
                return PageSnapshot(url, body_text, "", True, ())
            snapshot = self._page.evaluate(_READ_CARDS, {"url": url, "selectors": selectors})
            self._check_snapshot(url, revision)
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
            image_bytes, image_capture_failed = self._capture_image(card_locator, image, page_url)
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
    def _capture_image(card: object, selector: str, page_url: str) -> tuple[bytes | None, bool]:
        image = card.locator(selector)
        if not image.count():
            return None, False
        handle = None
        try:
            handle = image.first.element_handle(timeout=3_000)
            if not handle or not handle.evaluate(
                "(image, url) => image.isConnected && image.ownerDocument.URL === url", page_url
            ):
                return None, True
            if not handle.is_visible():
                return None, True
            captured = handle.screenshot(type="jpeg", quality=80, timeout=3_000)
            return (captured, False) if captured else (None, True)
        except Exception:
            return None, True
        finally:
            if handle is not None:
                handle.dispose()

    def _on_response(self, response: object) -> None:
        if getattr(response, "status", None) != 429:
            return
        self._http_status = 429
        try:
            headers = response.headers
            self._retry_after = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:
            self._retry_after = None


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
    except BrowserAdapterError:
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
