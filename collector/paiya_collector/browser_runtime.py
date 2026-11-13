from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin

from paiya_collector.browser_adapter import (
    LiveGroupConfig,
    PageSnapshot,
    VisibleComment,
    VisiblePost,
)


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


class PlaywrightVisiblePage:
    def __init__(self, page: object) -> None:
        self._page = page
        self._http_status: int | None = None
        self._retry_after: str | None = None
        page.on("response", self._on_response)

    def navigate(self, url: str) -> None:
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            if self._http_status == 429:
                return
            raise BrowserAdapterError(
                f"visible browser navigation failed ({type(exc).__name__}); source URL omitted"
            ) from None

    def snapshot(self, selectors: dict[str, str]) -> PageSnapshot:
        try:
            url = str(self._page.url)
            body_text = self._page.locator("body").inner_text(timeout=3_000)[:20_000]
            sort_label = _first_text(self._page.locator(selectors["sort_indicator"]))
            feed_end = _has_match(self._page, selectors.get("feed_end"))
            card_locator = self._page.locator(selectors["post_card"])
            card_count = card_locator.count()
            if card_count > 200:
                raise BrowserAdapterError("visible card count exceeded the 200-card read bound")

            posts = tuple(
                self._read_post(card_locator.nth(index), selectors, url, index)
                for index in range(card_count)
            )
            return PageSnapshot(
                url=url,
                body_text=body_text,
                sort_label=sort_label,
                feed_end=feed_end,
                posts=posts,
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
            self._page.evaluate("window.scrollBy(0, Math.max(640, window.innerHeight * 0.8))")
            self._page.wait_for_timeout(500)
        except Exception as exc:
            raise BrowserAdapterError(
                f"visible browser scroll failed ({type(exc).__name__}); source URL omitted"
            ) from None

    def _read_post(
        self,
        card: object,
        selectors: dict[str, str],
        page_url: str,
        index: int,
    ) -> VisiblePost:
        post_text = _first_text(card.locator(selectors["post_text"]))
        permalink_node = card.locator(selectors["permalink"])
        href = permalink_node.first.get_attribute("href") if permalink_node.count() else None
        permalink = urljoin(page_url, href or "")
        seller_name = _first_text(card.locator(selectors["seller_name"])) or None
        comments_locator = card.locator(selectors["comment"])
        comments_count = min(comments_locator.count(), 50)
        comments: list[VisibleComment] = []
        for comment_index in range(comments_count):
            comment = comments_locator.nth(comment_index)
            comment_text = _first_text(comment.locator(selectors["comment_text"]))
            if not comment_text:
                continue
            author_name = _first_text(comment.locator(selectors["comment_author"])) or None
            time_locator = comment.locator("time")
            source_time = None
            if time_locator.count():
                time_node = time_locator.first
                source_time = (
                    time_node.get_attribute("aria-label")
                    or time_node.get_attribute("datetime")
                    or _first_text(time_node)
                    or None
                )
            comment_key = (
                comment.get_attribute("data-commentid")
                or comment.get_attribute("data-comment-key")
                or None
            )
            comments.append(
                VisibleComment(
                    text=comment_text,
                    author_name=author_name,
                    comment_key=comment_key,
                    source_time=source_time,
                )
            )

        image_bytes, image_capture_failed = self._capture_image(card, selectors.get("image"))
        return VisiblePost(
            permalink=permalink,
            post_text=post_text,
            seller_name=seller_name,
            comments=tuple(comments),
            image_bytes=image_bytes,
            image_capture_failed=image_capture_failed,
            pinned=_has_match(card, selectors.get("pinned")),
            suggested=_has_match(card, selectors.get("suggested")),
            scroll_step=index,
            reported_comment_count=None,
        )

    @staticmethod
    def _capture_image(card: object, selector: str | None) -> tuple[bytes | None, bool]:
        if selector is None:
            return None, False
        image = card.locator(selector)
        if not image.count():
            return None, False
        if not image.first.is_visible():
            return None, True
        try:
            captured = image.first.screenshot(type="jpeg", quality=80, timeout=3_000)
            return (captured, False) if captured else (None, True)
        except Exception:
            return None, True

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
    try:
        browser = runtime.chromium.connect_over_cdp(endpoint, timeout=5_000)
        contexts = browser.contexts
        if not contexts:
            raise BrowserAdapterError("dedicated Chrome has no visible browser context")
        pages = contexts[0].pages
        if not pages:
            raise BrowserAdapterError("dedicated Chrome has no visible tab; reopen its profile")
        page = pages[0]
        visible_page = PlaywrightVisiblePage(page)
        yield visible_page
    except BrowserAdapterError:
        raise
    except Exception as exc:
        raise BrowserAdapterError(
            f"dedicated Chrome connection failed ({type(exc).__name__}); source URL omitted"
        ) from None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        runtime.stop()


def _first_text(locator: object) -> str:
    if locator.count() == 0:
        return ""
    return locator.first.inner_text(timeout=2_000).strip()


def _has_match(owner: object, selector: str | None) -> bool:
    return bool(selector and owner.locator(selector).count())
