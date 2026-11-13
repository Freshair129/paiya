from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest
from paiya_collector.browser_adapter import (
    PageSnapshot,
    RateLimitPause,
    VisibleComment,
    VisiblePost,
    capture_browser_feed,
    classify_page,
    feed_from_visible_posts,
    load_group_config,
    parse_retry_after,
    prune_expired_bundles,
    source_post_key_from_permalink,
)
from paiya_collector.html_adapter import parse_card

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_GRANT_ID = "44444444-5555-4666-8777-888888888888"


@pytest.mark.parametrize(
    "permalink",
    [
        "https://evil.example/groups/owner-selected/posts/123456789/",
        "http://www.facebook.com/groups/owner-selected/posts/123456789/",
        "https://user@www.facebook.com/groups/owner-selected/posts/123456789/",
        "https://www.facebook.com:444/groups/owner-selected/posts/123456789/",
    ],
)
def test_source_post_key_rejects_non_facebook_or_non_plain_permalink(permalink: str) -> None:
    assert source_post_key_from_permalink(permalink) is None


def test_source_post_key_accepts_plain_facebook_permalink() -> None:
    assert (
        source_post_key_from_permalink(
            "https://www.facebook.com/groups/owner-selected/posts/123456789/"
        )
        == "fb-post-123456789"
    )


def valid_config(tmp_path: Path) -> dict[str, object]:
    return {
        "group_key": "owner-group-01",
        "grant_id": SYNTHETIC_GRANT_ID,
        "group_url": "https://www.facebook.com/groups/owner-selected",
        "identity_scope": "owner-group-01",
        "adapter_version": "paiya-collector/0.1.0",
        "grant": {
            "active": True,
            "scopes": ["capture", "read", "media"],
            "retention_days": 7,
        },
        "page_size": 5,
        "overlap": 3,
        "scroll_budget": 30,
        "time_budget_seconds": 120,
        "selectors": {
            "post_card": "article[data-testid='feed-card']",
            "post_text": "[data-role='post-text']",
            "permalink": "a[data-role='permalink']",
            "seller_name": "[data-role='seller']",
            "comment": "[data-comment-key]",
            "comment_text": "[data-role='comment-text']",
            "comment_author": "[data-role='comment-author']",
            "image": "img[data-role='product-image']",
            "sort_indicator": "[data-role='sort']",
            "feed_end": "[data-role='feed-end']",
            "pinned": "[data-role='pinned']",
            "suggested": "[data-role='suggested']",
        },
    }


def write_config(path: Path, config: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_group_config_requires_owner_scope_active_grant_and_external_url_file(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repo"
    config_path = write_config(tmp_path / "local" / "owner-group-01.json", valid_config(tmp_path))

    loaded = load_group_config(config_path, repository_root=repository_root)

    assert loaded.group_key == "owner-group-01"
    assert loaded.grant_id == SYNTHETIC_GRANT_ID
    assert loaded.group_url == "https://www.facebook.com/groups/owner-selected"
    assert loaded.retention_days == 7
    assert set(loaded.scopes) == {"capture", "read", "media"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"group_key": "unapproved-group"}, "group_key"),
        (
            {
                "grant": {
                    "active": False,
                    "scopes": ["capture", "read", "media"],
                    "retention_days": 7,
                }
            },
            "active",
        ),
        ({"grant": {"active": True, "scopes": ["capture", "read"], "retention_days": 7}}, "scope"),
        (
            {
                "grant": {
                    "active": True,
                    "scopes": ["capture", "read", "media"],
                    "retention_days": 30,
                }
            },
            "retention",
        ),
        ({"group_url": "https://example.com/groups/owner-selected"}, "Facebook"),
    ],
)
def test_group_config_rejects_scope_or_source_outside_owner_approval(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    config = valid_config(tmp_path)
    config.update(change)
    path = write_config(tmp_path / "local" / "group.json", config)

    with pytest.raises(ValueError, match=message):
        load_group_config(path, repository_root=tmp_path / "repo")


def test_group_config_rejects_real_mapping_inside_git_checkout(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    path = write_config(repository_root / "local-group.json", valid_config(tmp_path))

    with pytest.raises(ValueError, match="outside the repository"):
        load_group_config(path, repository_root=repository_root)


def test_login_and_challenge_are_stop_conditions() -> None:
    assert (
        classify_page("https://www.facebook.com/login/", "Log in to Facebook") == "session_required"
    )
    assert (
        classify_page(
            "https://www.facebook.com/checkpoint/",
            "Security check",
        )
        == "challenge_required"
    )
    assert (
        classify_page(
            "https://www.facebook.com/groups/owner-selected",
            "ขาย RTX 3060 ราคา 5,500 บาท",
        )
        == "ok"
    )


def test_retry_after_accepts_delta_seconds_and_http_date_without_guessing() -> None:
    now = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)

    assert parse_retry_after("90", now=now) == now + timedelta(seconds=90)
    assert parse_retry_after(
        format_datetime(now + timedelta(minutes=5), usegmt=True),
        now=now,
    ) == now + timedelta(minutes=5)
    assert parse_retry_after(None, now=now) is None
    assert parse_retry_after("not-a-date", now=now) is None


def test_visible_post_conversion_preserves_evidence_without_saving_actor_names(
    tmp_path: Path,
) -> None:
    image_bytes = (REPO_ROOT / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    post = VisiblePost(
        permalink="https://www.facebook.com/groups/owner-selected/posts/987654321/",
        post_text="ขาย RTX 3060 12GB สภาพดี ราคา 5,500 บาท ค่าส่ง 50 บาท",
        seller_name="ร้านการ์ดจอสมมติ",
        comments=(
            VisibleComment(
                text="จองครับ",
                author_name="ผู้ซื้อสมมติ",
                comment_key="comment-01",
                source_time="2 นาที",
            ),
        ),
        image_bytes=image_bytes,
        pinned=False,
        suggested=False,
        scroll_step=0,
        reported_comment_count=1,
    )
    feed = feed_from_visible_posts(
        [post],
        sort_label="Newest posts",
        access="ok",
        session_result="ok",
        feed_end=True,
        scroll_step_seconds=0,
    )

    parsed = parse_card(
        feed.cards[0],
        fixture_path=tmp_path,
        group_key="owner-group-01",
        identity_scope="owner-group-01",
        observed_at="2026-11-13T05:00:00Z",
        image_bytes=feed.image_bytes,
        allow_local_image_files=feed.allow_local_image_files,
    )

    products = [item for item in parsed.payload["items"] if item["price"]["kind"] == "ask"]
    shipping = [item for item in parsed.payload["items"] if item["price"]["kind"] == "shipping"]
    assert products[0]["model"] == "RTX 3060 12GB"
    assert products[0]["price"]["amount"] == "5500.00"
    assert shipping[0]["price"]["amount"] == "50.00"
    assert parsed.payload["comments"][0]["text"] == "จองครับ"
    assert parsed.payload["comments"][0]["role"] == "other"
    assert parsed.payload["comments"][0]["actor_key"] is None
    assert "ผู้ซื้อสมมติ" not in json.dumps(parsed.payload, ensure_ascii=False)
    assert parsed.assets["e-image"]["mime"] == "image/jpeg"
    assert parsed.asset_bytes[next(iter(parsed.asset_bytes))] == image_bytes
    assert feed.sort_result == "verified_latest"
    assert parsed.source_post_key == "fb-post-987654321"


def test_failed_browser_image_capture_is_recorded_without_reading_config_directory(
    tmp_path: Path,
) -> None:
    image_bytes = (REPO_ROOT / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    (tmp_path / "browser-image-0.jpg").write_bytes(image_bytes)
    post = VisiblePost(
        permalink="https://www.facebook.com/groups/owner-selected/posts/987654321/",
        post_text="ขาย RTX 3060 ราคา 5,500 บาท",
        seller_name="ร้านสมมติ",
        image_capture_failed=True,
    )
    feed = feed_from_visible_posts(
        [post],
        sort_label="Newest posts",
        access="ok",
        session_result="ok",
        feed_end=True,
        scroll_step_seconds=0,
    )

    parsed = parse_card(
        feed.cards[0],
        fixture_path=tmp_path / "owner-group-01.json",
        group_key="owner-group-01",
        identity_scope="owner-group-01",
        observed_at="2026-11-13T05:00:00Z",
        image_bytes=feed.image_bytes,
        allow_local_image_files=feed.allow_local_image_files,
    )

    image_evidence = next(
        item for item in parsed.payload["evidence"] if item["local_key"] == "e-image"
    )
    assert image_evidence["media_state"] == "capture_failed"
    assert image_evidence["media_reason"] == "visible_image_capture_failed"
    assert parsed.assets == {}
    assert parsed.asset_bytes == {}


def test_unrecognized_sort_label_remains_unverified_and_missing_key_is_not_invented() -> None:
    post = VisiblePost(
        permalink="https://www.facebook.com/groups/owner-selected/posts/987654321/",
        post_text="ขาย RTX 3060 ราคา 5,500 บาท",
        seller_name="ร้านสมมติ",
    )

    feed = feed_from_visible_posts(
        [post],
        sort_label="โพสต์ที่เกี่ยวข้อง",
        access="ok",
        session_result="ok",
        feed_end=False,
        scroll_step_seconds=1,
    )
    no_key = feed_from_visible_posts(
        [VisiblePost(permalink="", post_text="โพสต์ไม่มีลิงก์")],
        sort_label="Newest posts",
        access="ok",
        session_result="ok",
        feed_end=False,
        scroll_step_seconds=1,
    )

    assert feed.sort_result == "unverified"
    assert no_key.cards[0].attrs["data-post-key"] == ""


def write_ready_run(
    incoming: Path,
    run_id: str,
    group_key: str,
    ended_at: datetime,
    *,
    ready: bool = True,
) -> Path:
    run_dir = incoming / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "envelopes").mkdir()
    (run_dir / "assets").mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "group_key": group_key,
                "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    if ready:
        (run_dir / "READY").write_bytes(b"")
    return run_dir


def test_retention_prunes_only_expired_ready_runs_for_the_configured_group(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)
    incoming = tmp_path / "inbox" / "incoming"
    incoming.mkdir(parents=True)
    expired = write_ready_run(
        incoming,
        "11111111-2222-4333-8444-555555555555",
        "owner-group-01",
        now - timedelta(days=8),
    )
    recent = write_ready_run(
        incoming,
        "22222222-3333-4444-8555-666666666666",
        "owner-group-01",
        now - timedelta(days=6),
    )
    other_group = write_ready_run(
        incoming,
        "33333333-4444-4555-8666-777777777777",
        "other-group",
        now - timedelta(days=8),
    )
    incomplete = write_ready_run(
        incoming,
        "44444444-5555-4666-8777-888888888888",
        "owner-group-01",
        now - timedelta(days=8),
        ready=False,
    )
    temporary = incoming / ".55555555-6666-4777-8888-999999999999.tmp"
    temporary.mkdir()

    removed = prune_expired_bundles(
        tmp_path / "inbox",
        group_key="owner-group-01",
        retention_days=7,
        now=now,
    )

    assert removed == ("11111111-2222-4333-8444-555555555555",)
    assert not expired.exists()
    assert recent.exists()
    assert other_group.exists()
    assert incomplete.exists()
    assert temporary.exists()


def test_retention_rejects_junction_in_inbox_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linked = tmp_path / "linked-inbox"

    def is_junction(candidate: Path) -> bool:
        return candidate == linked.absolute()

    monkeypatch.setattr(Path, "is_junction", is_junction)

    with pytest.raises(ValueError, match="symlink or junction"):
        prune_expired_bundles(
            linked,
            group_key="owner-group-01",
            retention_days=7,
            now=datetime.now(UTC),
        )


class FakeBrowserPage:
    def __init__(self, snapshots: list[PageSnapshot]) -> None:
        self.snapshots = snapshots
        self.snapshot_index = 0
        self.scroll_calls = 0

    def snapshot(self, _selectors: dict[str, str]) -> PageSnapshot:
        snapshot = self.snapshots[min(self.snapshot_index, len(self.snapshots) - 1)]
        self.snapshot_index += 1
        return snapshot

    def scroll_one_step(self) -> None:
        self.scroll_calls += 1


def load_test_config(tmp_path: Path):
    path = write_config(tmp_path / "local" / "owner-group-01.json", valid_config(tmp_path))
    return load_group_config(path, repository_root=tmp_path / "repo")


def post(key: str, *, text: str = "ขาย RTX 3060 ราคา 5,500 บาท", step: int = 0) -> VisiblePost:
    return VisiblePost(
        permalink=f"https://www.facebook.com/groups/owner-selected/posts/{key}/",
        post_text=text,
        seller_name="ร้านสมมติ",
        scroll_step=step,
    )


def snapshot(
    posts: tuple[VisiblePost, ...],
    *,
    sort_label: str = "Newest posts",
    feed_end: bool = False,
    url: str = "https://www.facebook.com/groups/owner-selected",
    body_text: str = "Group posts",
    retry_after: str | None = None,
    http_status: int | None = None,
) -> PageSnapshot:
    return PageSnapshot(
        url=url,
        body_text=body_text,
        sort_label=sort_label,
        feed_end=feed_end,
        posts=posts,
        retry_after=retry_after,
        http_status=http_status
        if http_status is not None
        else (429 if retry_after is not None else None),
    )


def test_browser_capture_stops_at_overlap_before_an_extra_scroll(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    overlap_posts = (
        post("123456789"),
        post("123456790"),
        post("123456791"),
    )
    page = FakeBrowserPage([snapshot(overlap_posts)])

    feed = capture_browser_feed(
        page,
        config=config,
        previously_published={
            "fb-post-123456789",
            "fb-post-123456790",
            "fb-post-123456791",
        },
        now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
        monotonic=lambda: 0.0,
    )

    assert feed.cards[0].attrs["data-post-key"] == "fb-post-123456789"
    assert feed.forced_stop_reason is None
    assert page.scroll_calls == 0


def test_pinned_post_is_preserved_but_does_not_count_toward_overlap(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    pinned = VisiblePost(
        permalink="https://www.facebook.com/groups/owner-selected/posts/123456788/",
        post_text="โพสต์ปักหมุด",
        pinned=True,
    )
    prior_posts = (post("123456789"), post("123456790"), post("123456791"))
    page = FakeBrowserPage([snapshot((pinned, *prior_posts))])

    feed = capture_browser_feed(
        page,
        config=config,
        previously_published={
            "fb-post-123456789",
            "fb-post-123456790",
            "fb-post-123456791",
        },
        now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
        monotonic=lambda: 0.0,
    )

    assert [card.attrs["data-post-key"] for card in feed.cards] == [
        "fb-post-123456788",
        "fb-post-123456789",
        "fb-post-123456790",
        "fb-post-123456791",
    ]
    assert feed.cards[0].attrs["data-pinned"] == "true"
    assert page.scroll_calls == 0


def test_browser_capture_rejects_a_different_facebook_group(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    page = FakeBrowserPage(
        [
            snapshot(
                (post("123456789"),),
                url="https://www.facebook.com/groups/a-different-group",
            )
        ]
    )

    with pytest.raises(ValueError, match="configured group"):
        capture_browser_feed(
            page,
            config=config,
            previously_published=set(),
            now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
            monotonic=lambda: 0.0,
        )


def test_browser_capture_observes_scroll_and_marks_budget_stop_without_sort_claim(
    tmp_path: Path,
) -> None:
    config = valid_config(tmp_path)
    config.update({"page_size": 1, "scroll_budget": 0})
    config_path = write_config(tmp_path / "local" / "owner-group-01.json", config)
    loaded = load_group_config(config_path, repository_root=tmp_path / "repo")
    page = FakeBrowserPage(
        [snapshot((post("100000001"),), sort_label="Relevant posts", feed_end=False)]
    )

    feed = capture_browser_feed(
        page,
        config=loaded,
        previously_published=set(),
        now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
        monotonic=lambda: 0.0,
    )

    assert feed.sort_result == "unverified"
    assert feed.forced_stop_reason == "scroll_budget_exhausted"
    assert feed.feed_end is False
    assert page.scroll_calls == 0


@pytest.mark.parametrize(
    ("url", "body_text", "expected_access", "expected_session"),
    [
        ("https://www.facebook.com/login/", "Log in to Facebook", "session_required", "required"),
        ("https://www.facebook.com/checkpoint/", "Security check", "challenge_required", "unknown"),
    ],
)
def test_browser_capture_returns_empty_stop_bundle_state_without_scrolling(
    tmp_path: Path,
    url: str,
    body_text: str,
    expected_access: str,
    expected_session: str,
) -> None:
    config = load_test_config(tmp_path)
    page = FakeBrowserPage([snapshot((), url=url, body_text=body_text, feed_end=False)])

    feed = capture_browser_feed(
        page,
        config=config,
        previously_published={"fb-post-123456789"},
        now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
        monotonic=lambda: 0.0,
    )

    assert feed.access == expected_access
    assert feed.session_result == expected_session
    assert feed.cards == ()
    assert page.scroll_calls == 0


def test_browser_capture_raises_rate_limit_pause_using_retry_after_header(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)
    now = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)
    page = FakeBrowserPage([snapshot((), retry_after="120")])

    with pytest.raises(RateLimitPause) as exc_info:
        capture_browser_feed(
            page,
            config=config,
            previously_published=set(),
            now=now,
            monotonic=lambda: 0.0,
        )

    assert exc_info.value.retry_after_until == now + timedelta(seconds=120)
    assert page.scroll_calls == 0


def test_browser_capture_with_429_without_retry_after_remains_paused(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    page = FakeBrowserPage([snapshot((), http_status=429)])

    with pytest.raises(RateLimitPause) as exc_info:
        capture_browser_feed(
            page,
            config=config,
            previously_published=set(),
            now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
            monotonic=lambda: 0.0,
        )

    assert exc_info.value.retry_after_until is None
    assert page.scroll_calls == 0
