from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from paiya_collector.browser_runtime import (
    BrowserAdapterError,
    PlaywrightVisiblePage,
    attach_dedicated_browser,
    read_devtools_endpoint,
)


def make_profile(path: Path, *, group_key: str = "owner-group-01", port: str = "9333") -> Path:
    path.mkdir(parents=True)
    (path / "PAIYA_COLLECTOR_PROFILE").write_text(group_key, encoding="utf-8")
    (path / "DevToolsActivePort").write_text(
        f"{port}\n/devtools/browser/synthetic\n", encoding="utf-8"
    )
    return path


def test_devtools_endpoint_uses_only_marked_dedicated_profile_and_loopback(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    profile = make_profile(tmp_path / "dedicated-profile")

    endpoint = read_devtools_endpoint(
        profile,
        group_key="owner-group-01",
        repository_root=repo_root,
    )

    assert endpoint == "http://127.0.0.1:9333"


def test_devtools_endpoint_rejects_missing_or_mismatched_dedicated_profile_marker(
    tmp_path: Path,
) -> None:
    profile = make_profile(tmp_path / "wrong-profile", group_key="other-group")

    with pytest.raises(ValueError, match="dedicated browser profile"):
        read_devtools_endpoint(
            profile,
            group_key="owner-group-01",
            repository_root=tmp_path / "repo",
        )


def test_devtools_endpoint_rejects_profile_inside_repository_or_invalid_port(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    in_repo = make_profile(repo_root / "browser-profile")
    with pytest.raises(ValueError, match="outside the repository"):
        read_devtools_endpoint(
            in_repo,
            group_key="owner-group-01",
            repository_root=repo_root,
        )

    profile = make_profile(tmp_path / "invalid-port", port="70000")
    with pytest.raises(ValueError, match="DevToolsActivePort"):
        read_devtools_endpoint(
            profile,
            group_key="owner-group-01",
            repository_root=repo_root,
        )


def test_attaching_to_dedicated_browser_does_not_navigate_before_manual_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = make_profile(tmp_path / "dedicated-profile")

    class FakePage:
        def __init__(self) -> None:
            self.goto_calls: list[str] = []

        def on(self, *_args: Any) -> None:
            pass

        def goto(self, url: str, **_kwargs: Any) -> None:
            self.goto_calls.append(url)

    page = FakePage()

    class FakeBrowser:
        contexts = [type("Context", (), {"pages": [page]})()]
        version = "145.0.1"

        def close(self) -> None:
            pass

    browser = FakeBrowser()
    runtime = type(
        "Runtime",
        (),
        {
            "chromium": type(
                "Chromium",
                (),
                {"connect_over_cdp": staticmethod(lambda *_args, **_kwargs: browser)},
            )(),
            "stop": lambda self: None,
        },
    )()
    playwright_module = ModuleType("playwright")
    sync_api_module = ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = lambda: type(
        "Manager", (), {"start": staticmethod(lambda: runtime)}
    )()
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    config = type(
        "Config",
        (),
        {"group_key": "owner-group-01", "group_url": "https://www.facebook.com/groups/example"},
    )()
    with attach_dedicated_browser(
        profile,
        config=config,
        repository_root=tmp_path / "repo",
    ) as visible_page:
        assert visible_page is not None

    assert page.goto_calls == []


def test_navigation_keeps_current_group_feed_with_user_selected_sort(monkeypatch) -> None:
    class Page:
        url = "https://www.facebook.com/groups/example/?sorting_setting=CHRONOLOGICAL"

        def __init__(self):
            self.requested = []

        def on(self, *_args):
            pass

        def goto(self, url, **_kwargs):
            self.requested.append(url)
            self.url = url

    page = Page()
    guarded = PlaywrightVisiblePage(page, "https://www.facebook.com/groups/example/")
    guard_calls = []
    monkeypatch.setattr(guarded, "_guard_navigation", lambda: guard_calls.append(True))

    guarded.navigate("https://www.facebook.com/groups/example/")

    assert guard_calls == [True]
    assert page.requested == [
        "https://www.facebook.com/groups/example/?sorting_setting=CHRONOLOGICAL"
    ]


@pytest.mark.parametrize("failure", ["permalink_changed", "image_detached"])
def test_image_capture_rejects_changes_during_screenshot(failure) -> None:
    class Image:
        def is_visible(self):
            return True

        def screenshot(self, **_kwargs):
            return b"synthetic image"

        def dispose(self):
            pass

    image = Image()

    class CardHandle:
        def __init__(self):
            self.identity_checks = 0
            self.containment_checks = 0

        def evaluate(self, _script, argument):
            if argument is image:
                self.containment_checks += 1
                return not (failure == "image_detached" and self.containment_checks == 2)
            self.identity_checks += 1
            return not (failure == "permalink_changed" and self.identity_checks == 2)

        def query_selector(self, _selector):
            return image

        def dispose(self):
            pass

    handle = CardHandle()

    class Card:
        def count(self):
            return 1

        def element_handle(self, **_kwargs):
            return handle

    captured, failed = PlaywrightVisiblePage._capture_image(
        Card(), ".photo", "https://example.test/groups/test", "a", "/posts/1"
    )

    assert captured is None
    assert failed


def test_image_capture_does_not_wait_for_disappeared_card() -> None:
    class Card:
        def count(self):
            return 0

        def element_handle(self, **_kwargs):
            pytest.fail("missing cards must not wait for an element handle")

    captured, failed = PlaywrightVisiblePage._capture_image(
        Card(), ".photo", "https://example.test/groups/test", "a", "/posts/1"
    )

    assert captured is None
    assert failed


@pytest.mark.parametrize(
    "current_url",
    [
        "about:blank",
        "https://www.facebook.com/groups/other/",
        "https://www.facebook.com/groups/example/posts/123/",
        "https://other.invalid/groups/example/",
        "https://www.facebook.com/groups/example/?sorting_setting=CHRONOLOGICAL&extra=1",
        "https://www.facebook.com/groups/example/?sorting_setting=CHRONOLOGICAL#section",
    ],
)
def test_navigation_opens_configured_feed_from_other_pages(monkeypatch, current_url) -> None:
    class Page:
        url = current_url

        def on(self, *_args):
            pass

        def goto(self, url, **_kwargs):
            self.url = url

    page = Page()
    guarded = PlaywrightVisiblePage(page, "https://www.facebook.com/groups/example/")
    monkeypatch.setattr(guarded, "_guard_navigation", lambda: None)

    guarded.navigate("https://www.facebook.com/groups/example/")

    assert page.url == "https://www.facebook.com/groups/example/"


@pytest.mark.parametrize(
    "url",
    [
        "https://other.invalid/groups/example",
        "https://m.facebook.com/groups/example",
        "https://www.facebook.com/groups/wrong",
    ],
)
def test_snapshot_checks_origin_and_group_before_touching_dom(url) -> None:
    class ForeignPage:
        def on(self, *_args):
            pass

        def locator(self, selector):
            pytest.fail(f"read forbidden DOM selector {selector}")

    page = ForeignPage()
    page.url = url
    guarded = PlaywrightVisiblePage(page, "https://www.facebook.com/groups/example")
    with pytest.raises(BrowserAdapterError):
        guarded.snapshot({})


@pytest.mark.parametrize("path", ["/login/", "/checkpoint/"])
def test_same_origin_session_stop_does_not_read_dom(path) -> None:
    class SessionPage:
        url = f"https://www.facebook.com{path}"

        def on(self, *_args):
            pass

        def locator(self, selector):
            pytest.fail(f"read forbidden session DOM selector {selector}")

    guarded = PlaywrightVisiblePage(SessionPage(), "https://www.facebook.com/groups/example")
    snapshot = guarded.snapshot({})
    assert snapshot.posts == ()
    assert snapshot.body_text == ""


def test_dom_change_during_read_discards_snapshot() -> None:
    class Page:
        url = "https://www.facebook.com/groups/example"

        def on(self, *_args):
            pass

        def evaluate(self, script, expected):
            self.url = "https://www.facebook.com/groups/wrong"
            assert "location.href === url" in script
            assert expected != self.url
            return None

        def locator(self, selector):
            pytest.fail(f"read forbidden DOM selector {selector}")

    guarded = PlaywrightVisiblePage(Page(), "https://www.facebook.com/groups/example")
    with pytest.raises(BrowserAdapterError, match="document changed during capture"):
        guarded.snapshot({})
