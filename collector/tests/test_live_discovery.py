from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from paiya_collector.browser_adapter import (
    PageSnapshot,
    RateLimitPause,
    VisibleComment,
    VisiblePost,
)
from paiya_collector.live import discover_browser, group_run_lock

GROUP_KEY = "owner-group-01"
GRANT_ID = "44444444-5555-4666-8777-888888888888"


def write_live_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "group_key": GROUP_KEY,
        "grant_id": GRANT_ID,
        "group_url": "https://www.facebook.com/groups/owner-selected",
        "identity_scope": GROUP_KEY,
        "adapter_version": "paiya-collector/0.1.0",
        "grant": {"active": True, "scopes": ["capture", "read", "media"], "retention_days": 7},
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
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class FakePage:
    def __init__(self, snapshots: list[PageSnapshot]) -> None:
        self.snapshots = snapshots
        self.snapshot_index = 0
        self.navigated_to: str | None = None
        self.scroll_calls = 0

    def navigate(self, url: str) -> None:
        self.navigated_to = url

    def snapshot(self, _selectors: dict[str, str]) -> PageSnapshot:
        result = self.snapshots[min(self.snapshot_index, len(self.snapshots) - 1)]
        self.snapshot_index += 1
        return result

    def scroll_one_step(self) -> None:
        self.scroll_calls += 1


def fake_attacher(page: FakePage):
    @contextmanager
    def attach(_profile_dir: Path, *, config, repository_root: Path) -> Iterator[FakePage]:
        assert config.group_key == GROUP_KEY
        assert repository_root.name == "repo"
        yield page

    return attach


def post(image: bytes) -> VisiblePost:
    return VisiblePost(
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
        image_bytes=image,
        reported_comment_count=1,
    )


def page_snapshot(
    posts: tuple[VisiblePost, ...],
    *,
    feed_end: bool = True,
    access_url: str = "https://www.facebook.com/groups/owner-selected",
    body_text: str = "Group posts",
    http_status: int | None = None,
    retry_after: str | None = None,
) -> PageSnapshot:
    return PageSnapshot(
        url=access_url,
        body_text=body_text,
        sort_label="Newest posts",
        feed_end=feed_end,
        posts=posts,
        retry_after=retry_after,
        http_status=http_status,
    )


def invoke_live(
    tmp_path: Path,
    *,
    page: FakePage,
    run_id: str,
    clear_manual_rate_limit_pause: bool = False,
) -> dict[str, object]:
    repo_root = tmp_path / "repo"
    config_path = write_live_config(tmp_path / "local" / "owner-group-01.json")
    return discover_browser(
        config_path,
        profile_dir=tmp_path / "dedicated-profile",
        inbox_root=tmp_path / "inbox",
        state_dir=tmp_path / "state",
        run_id_arg=run_id,
        browser_attacher=fake_attacher(page),
        repository_root=repo_root,
        now=datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
        clear_manual_rate_limit_pause=clear_manual_rate_limit_pause,
    )


def test_live_discovery_publishes_v1_bundle_from_visible_snapshot_only(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    image = (repo_root / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    page = FakePage([page_snapshot((post(image),))])
    run_id = "55555555-6666-4777-8888-999999999999"

    report = invoke_live(tmp_path, page=page, run_id=run_id)

    assert report["ok"] is True
    assert report["coverage"] == "complete"
    assert report["cards_seen"] == 1
    assert page.navigated_to == "https://www.facebook.com/groups/owner-selected"
    bundle = Path(report["bundle_path"])
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert run["group_key"] == GROUP_KEY
    assert run["grant_id"] == GRANT_ID
    assert "group_url" not in run
    envelope = json.loads(next((bundle / "envelopes").glob("*.json")).read_text(encoding="utf-8"))
    assert envelope["source_kind"] == "facebook_live"
    assert envelope["payload"]["items"][0]["price"]["amount"] == "5500.00"
    assert envelope["payload"]["comments"][0]["role"] == "other"
    assert envelope["payload"]["comments"][0]["actor_key"] is None
    assert envelope["assets"]["e-image"]["mime"] == "image/jpeg"


def test_changed_content_for_same_key_keeps_first_sighting_and_reports_conflict(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    image = (repo_root / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    changed = VisiblePost(
        permalink=post(image).permalink,
        post_text="ขาย RTX 3060 ราคา 6,000 บาท",
        seller_name="ร้านการ์ดจอสมมติ",
        image_bytes=image,
        scroll_step=1,
    )
    page = FakePage(
        [
            page_snapshot((post(image),), feed_end=False),
            page_snapshot((changed,), feed_end=True),
        ]
    )

    report = invoke_live(
        tmp_path,
        page=page,
        run_id="88888888-9999-4000-8111-222222222222",
    )

    assert report["cards_seen"] == 2
    assert report["cards_parsed"] == 2
    assert report["duplicate_skips"] == [
        {"source_post_key": "fb-post-987654321", "content_changed": True}
    ]
    bundle = Path(report["bundle_path"])
    envelope = json.loads(next((bundle / "envelopes").glob("*.json")).read_text(encoding="utf-8"))
    assert envelope["payload"]["items"][0]["price"]["amount"] == "5500.00"


def test_replaying_published_run_does_not_navigate_or_publish_again(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    image = (repo_root / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    run_id = "99999999-0000-4111-8222-333333333333"
    first_page = FakePage([page_snapshot((post(image),))])
    first = invoke_live(tmp_path, page=first_page, run_id=run_id)
    run_names_before = sorted(path.name for path in (tmp_path / "inbox" / "incoming").iterdir())
    replay_page = FakePage([page_snapshot((post(image),))])

    replay = invoke_live(tmp_path, page=replay_page, run_id=run_id)

    assert replay["replayed"] is True
    assert replay_page.navigated_to is None
    assert (
        sorted(path.name for path in (tmp_path / "inbox" / "incoming").iterdir())
        == run_names_before
    )
    assert replay["bundle_path"] == first["bundle_path"]


def test_live_challenge_publishes_none_bundle_and_keeps_checkpoint(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    image = (repo_root / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    first = FakePage([page_snapshot((post(image),))])
    first_report = invoke_live(
        tmp_path,
        page=first,
        run_id="55555555-6666-4777-8888-999999999999",
    )
    state_path = tmp_path / "state" / f"{GROUP_KEY}.json"
    checkpoint_before = json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"]

    challenge = FakePage(
        [
            page_snapshot(
                (),
                access_url="https://www.facebook.com/checkpoint/",
                body_text="Security check",
            )
        ]
    )
    report = invoke_live(
        tmp_path,
        page=challenge,
        run_id="66666666-7777-4888-8999-000000000000",
    )

    assert first_report["coverage"] == "complete"
    assert report["coverage"] == "none"
    assert report["error_code"] == "challenge_required"
    assert report["stop_reason"] == "access_blocked"
    assert json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"] == checkpoint_before


def test_live_429_persists_retry_after_and_leaves_checkpoint_and_inbox_unchanged(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    image = (repo_root / "fixtures" / "images" / "rtx3060.jpg").read_bytes()
    first = FakePage([page_snapshot((post(image),))])
    invoke_live(tmp_path, page=first, run_id="55555555-6666-4777-8888-999999999999")
    state_path = tmp_path / "state" / f"{GROUP_KEY}.json"
    checkpoint_before = state_path.read_bytes()
    runs_before = sorted(path.name for path in (tmp_path / "inbox" / "incoming").iterdir())

    limited = FakePage([page_snapshot((), http_status=429, retry_after="120")])
    with pytest.raises(RateLimitPause) as exc_info:
        invoke_live(tmp_path, page=limited, run_id="77777777-8888-4999-8000-111111111111")

    pause_path = tmp_path / "state" / f"{GROUP_KEY}.rate-limit.json"
    pause = json.loads(pause_path.read_text(encoding="utf-8"))
    assert exc_info.value.retry_after_until == datetime(2026, 11, 13, 5, 2, tzinfo=UTC)
    assert pause["retry_after_until"] == "2026-11-13T05:02:00Z"
    assert state_path.read_bytes() == checkpoint_before
    assert sorted(path.name for path in (tmp_path / "inbox" / "incoming").iterdir()) == runs_before
    assert "stop_reason" not in pause
    assert "error_code" not in pause


def test_live_429_without_retry_after_requires_explicit_manual_wait_and_resume(
    tmp_path: Path,
) -> None:
    limited = FakePage([page_snapshot((), http_status=429)])
    with pytest.raises(RateLimitPause) as exc_info:
        invoke_live(
            tmp_path,
            page=limited,
            run_id="aaaaaaa1-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        )
    assert exc_info.value.manual_resume_required is True

    premature = FakePage([page_snapshot(())])
    with pytest.raises(RateLimitPause) as repeat:
        invoke_live(
            tmp_path,
            page=premature,
            run_id="aaaaaaa2-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        )
    assert repeat.value.manual_resume_required is True
    assert premature.navigated_to is None

    resumed = FakePage([page_snapshot(())])
    report = invoke_live(
        tmp_path,
        page=resumed,
        run_id="aaaaaaa3-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        clear_manual_rate_limit_pause=True,
    )
    assert report["coverage"] == "none"
    assert resumed.navigated_to == "https://www.facebook.com/groups/owner-selected"


def test_existing_group_lock_is_not_removed_automatically(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lock_path = state_dir / f".{GROUP_KEY}.run.lock"
    lock_path.write_text(
        json.dumps(
            {
                "group_key": GROUP_KEY,
                "run_id": "bbbbbbb1-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "process_id": -1,
                "started_at": "2026-11-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already active"):
        with group_run_lock(
            state_dir,
            GROUP_KEY,
            "bbbbbbb2-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        ):
            raise AssertionError("a second run must not enter while the lock exists")

    assert lock_path.is_file()
