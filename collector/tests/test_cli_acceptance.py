from __future__ import annotations

import html
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "fixtures" / "rtx3060.html"
GROUP_CONFIG = REPO_ROOT / "fixtures" / "group.json"
SYNTHETIC_GRANT_ID = "22222222-3333-4444-8555-666666666666"


def invoke_discover(
    tmp_path: Path,
    *,
    run_id: str,
    fixture: Path = FIXTURE,
    group_config: Path = GROUP_CONFIG,
    inbox: Path | None = None,
    state_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "-m",
        "paiya_collector.cli",
        "discover",
        "--fixture",
        str(fixture),
        "--group-config",
        str(group_config),
        "--inbox",
        str(inbox or (tmp_path / "inbox")),
        "--run-id",
        run_id,
    ]
    args.extend(["--state-dir", str(state_dir or (tmp_path / "state"))])
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_complete_thai_post_publishes_validator_ready_bundle(tmp_path: Path) -> None:
    run_id = "11111111-2222-4333-8444-555555555555"
    result = invoke_discover(tmp_path, run_id=run_id)

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["run_id"] == run_id
    assert Path(summary["bundle_path"]).is_absolute()
    assert summary["cards_seen"] == 1
    assert summary["cards_parsed"] == 1
    assert summary["coverage"] == "complete"
    assert summary["stop_reason"] == "feed_end"

    bundle = Path(summary["bundle_path"])
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert run["group_key"] == "synthetic-thai-gpu-group"
    assert run["grant_id"] == SYNTHETIC_GRANT_ID
    assert (bundle / "READY").read_bytes() == b""
    assert (bundle / "envelopes").is_dir()
    assert (bundle / "assets").is_dir()

    envelope_path = next((bundle / "envelopes").glob("*.json"))
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    assert envelope["source_kind"] == "facebook_live"
    assert envelope["grant_id"] == SYNTHETIC_GRANT_ID
    assert payload["source_post_key"] == "syn-post-rtx3060-001"
    assert envelope["capture_id"] == str(uuid.uuid5(uuid.UUID(run_id), payload["source_post_key"]))
    assert envelope_path.name == f"{envelope['capture_id']}.json"
    assert payload["seller"]["display_name"] == "ร้านการ์ดจอสมมติ"

    products = [item for item in payload["items"] if item["price"]["kind"] == "ask"]
    shipping = [item for item in payload["items"] if item["price"]["kind"] == "shipping"]
    assert [(item["model"], item["price"]["amount"]) for item in products] == [
        ("RTX 3060 12GB", "5500.00")
    ]
    assert len(shipping) == 1
    assert shipping[0]["price"]["amount"] == "50.00"
    assert shipping[0]["source_item_key"] is None

    buyer_comments = [comment for comment in payload["comments"] if comment["text"] == "จองครับ"]
    assert len(buyer_comments) == 1
    assert buyer_comments[0]["role"] == "other"
    assert payload["activity"]["distinct_comments_seen"] == 1

    comment_evidence = [
        evidence
        for evidence in payload["evidence"]
        if evidence["source_span"] is not None
        and evidence["source_span"]["field"] == "comment_text"
    ]
    assert len(comment_evidence) == 1
    assert comment_evidence[0]["role"] == "other"
    assert comment_evidence[0]["source_span"]["comment_key"] == "buyer-comment-001"
    image_evidence = next(
        evidence for evidence in payload["evidence"] if evidence["media_state"] == "captured"
    )
    image_asset = envelope["assets"][image_evidence["local_key"]]
    assert image_asset["mime"] == "image/jpeg"
    assert image_asset["bytes"] > 0
    assert len(image_asset["sha256"]) == 64
    assert (bundle / "assets" / image_asset["staging_key"]).is_file()


def write_feed_fixture(
    path: Path,
    post_keys: list[str],
    *,
    sort_result: str = "verified_latest",
    feed_end: bool = True,
    access: str = "ok",
    pinned_keys: set[str] | None = None,
    step_by_key: dict[str, int] | None = None,
    post_texts: list[str] | None = None,
) -> Path:
    pinned_keys = pinned_keys or set()
    step_by_key = step_by_key or {}
    cards = []
    if post_texts is not None and len(post_texts) != len(post_keys):
        raise ValueError("post_texts must have one value per card")
    for index, key in enumerate(post_keys):
        pinned = "true" if key in pinned_keys else "false"
        step = step_by_key.get(key, index // 5)
        safe_key = html.escape(key, quote=True)
        post_text = html.escape(
            post_texts[index] if post_texts else "ขาย RTX 3060 สภาพดี ราคา 5,500 บาท"
        )
        cards.append(
            f'''<article data-testid="feed-card" data-post-key="{safe_key}"
                data-permalink="https://synthetic.facebook.invalid/groups/synthetic-gpu/posts/{safe_key}/"
                data-seller-key="seller-01" data-seller-name="ร้านการ์ดจอสมมติ"
                data-posted-at="5 ชั่วโมง" data-comment-count="0"
                data-pinned="{pinned}" data-suggested="false" data-scroll-step="{step}">
                <div data-role="post-text">{post_text}</div>
              </article>'''
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<!doctype html><html lang="th"><body>'
        f'<main data-testid="feed" data-access="{html.escape(access, quote=True)}" '
        'data-session-result="ok" '
        f'data-sort-result="{sort_result}" '
        f'data-feed-end="{str(feed_end).lower()}" data-scroll-step-seconds="2">'
        + "\n".join(cards)
        + "</main></body></html>",
        encoding="utf-8",
    )
    return path


def test_unverified_sort_keeps_checkpoint_and_pinned_cards_do_not_break_overlap(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "collector-state"
    inbox = tmp_path / "inbox"
    initial_fixture = write_feed_fixture(
        tmp_path / "initial.html", ["syn-old-1", "syn-old-2", "syn-old-3"]
    )
    initial = invoke_discover(
        tmp_path,
        run_id="aaaaaaaa-2222-4333-8444-555555555555",
        fixture=initial_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr
    initial_summary = json.loads(initial.stdout)
    assert initial_summary["coverage"] == "complete"
    state_path = state_dir / "synthetic-thai-gpu-group.json"
    checkpoint_before = json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"]
    assert checkpoint_before["top_keys"] == ["syn-old-1", "syn-old-2", "syn-old-3"]

    unverified_fixture = write_feed_fixture(
        tmp_path / "unverified.html",
        ["syn-pin", "syn-old-1", "syn-old-2", "syn-old-3"],
        sort_result="unverified",
        pinned_keys={"syn-pin"},
    )
    partial = invoke_discover(
        tmp_path,
        run_id="bbbbbbbb-2222-4333-8444-555555555555",
        fixture=unverified_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert partial.returncode == 0, partial.stdout + partial.stderr
    summary = json.loads(partial.stdout)
    assert summary["cards_seen"] == 4
    assert summary["cards_parsed"] == 4
    assert summary["coverage"] == "partial"
    assert summary["stop_reason"] == "overlap_reached"
    assert summary["partial_reasons"] == ["sort_unverified"]
    checkpoint_after = json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"]
    assert checkpoint_after == checkpoint_before


def test_identical_duplicate_card_is_published_once_and_run_id_is_idempotent(
    tmp_path: Path,
) -> None:
    fixture = write_feed_fixture(tmp_path / "duplicate.html", ["syn-duplicate", "syn-duplicate"])
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"
    run_id = "cccccccc-2222-4333-8444-555555555555"

    first = invoke_discover(
        tmp_path,
        run_id=run_id,
        fixture=fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    report = json.loads(first.stdout)
    assert report["cards_seen"] == 2
    assert report["cards_parsed"] == 2
    assert report["duplicate_cards_skipped"] == 1
    assert report["duplicate_skips"] == [
        {"source_post_key": "syn-duplicate", "content_changed": False}
    ]
    bundle = Path(report["bundle_path"])
    assert len(list((bundle / "envelopes").glob("*.json"))) == 1
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert run["duplicate_cards_skipped"] == 1
    assert run["duplicate_skips"] == report["duplicate_skips"]

    retry = invoke_discover(
        tmp_path,
        run_id=run_id,
        fixture=fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert retry.returncode == 0, retry.stdout + retry.stderr
    replay = json.loads(retry.stdout)
    assert replay["replayed"] is True
    assert Path(replay["bundle_path"]) == bundle
    assert len(list((inbox / "incoming").iterdir())) == 1


def test_all_cards_without_stable_keys_produce_none_coverage(tmp_path: Path) -> None:
    fixture = write_feed_fixture(tmp_path / "unreadable.html", [""])
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"

    result = invoke_discover(
        tmp_path,
        run_id="cccccccc-3333-4444-8555-666666666666",
        fixture=fixture,
        inbox=inbox,
        state_dir=state_dir,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    bundle = Path(report["bundle_path"])
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert report["cards_seen"] == 1
    assert report["cards_parsed"] == 0
    assert report["coverage"] == "none"
    assert report["partial_reasons"] == []
    assert run["coverage"] == "none"
    assert run["partial_reasons"] == []
    assert run["error_code"] == "adapter_error"
    assert list((bundle / "envelopes").iterdir()) == []
    assert (
        json.loads((state_dir / "synthetic-thai-gpu-group.json").read_text(encoding="utf-8"))[
            "checkpoint"
        ]
        is None
    )


def test_empty_feed_publishes_none_bundle_with_empty_directories(tmp_path: Path) -> None:
    fixture = write_feed_fixture(
        tmp_path / "empty.html", [], sort_result="unverified", feed_end=True
    )
    result = invoke_discover(
        tmp_path,
        run_id="cccccccc-4444-4555-8666-777777777777",
        fixture=fixture,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    bundle = Path(report["bundle_path"])
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert report["cards_seen"] == 0
    assert report["cards_parsed"] == 0
    assert report["coverage"] == "none"
    assert report["partial_reasons"] == []
    assert run["error_code"] == "adapter_error"
    assert list((bundle / "envelopes").iterdir()) == []
    assert list((bundle / "assets").iterdir()) == []


def test_changed_duplicate_keeps_first_publishes_skip_and_continues(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"
    initial_fixture = write_feed_fixture(
        tmp_path / "first.html", ["syn-prior-1", "syn-prior-2", "syn-prior-3"]
    )
    initial = invoke_discover(
        tmp_path,
        run_id="dddddddd-2222-4333-8444-555555555555",
        fixture=initial_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr
    state_path = state_dir / "synthetic-thai-gpu-group.json"
    checkpoint_before = json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"]

    changed_duplicate_fixture = write_feed_fixture(
        tmp_path / "conflict.html",
        ["syn-changing", "syn-changing", "syn-after-duplicate"],
        post_texts=[
            "ขาย RTX 3060 ราคา 5,500 บาท",
            "ขาย RTX 3060 ราคา 6,000 บาท",
            "ขาย RTX 3060 ราคา 4,900 บาท",
        ],
    )
    second_run = invoke_discover(
        tmp_path,
        run_id="eeeeeeee-2222-4333-8444-555555555555",
        fixture=changed_duplicate_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert second_run.returncode == 0, second_run.stdout + second_run.stderr
    report = json.loads(second_run.stdout)
    assert report["cards_seen"] == 3
    assert report["cards_parsed"] == 3
    assert report["duplicate_cards_skipped"] == 1
    assert report["duplicate_skips"] == [
        {"source_post_key": "syn-changing", "content_changed": True}
    ]
    assert report["coverage"] == "partial"
    assert report["partial_reasons"] == ["feed_stopped_before_overlap"]
    bundle = Path(report["bundle_path"])
    envelopes = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (bundle / "envelopes").glob("*.json")
    ]
    payloads = {
        envelope["payload"]["source_post_key"]: envelope["payload"] for envelope in envelopes
    }
    assert set(payloads) == {"syn-changing", "syn-after-duplicate"}
    changing_price = payloads["syn-changing"]["items"][0]["price"]
    assert changing_price["amount"] == "5500.00"
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert run["duplicate_skips"] == report["duplicate_skips"]
    assert len(list((inbox / "incoming").iterdir())) == 2
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["checkpoint"] == checkpoint_before


def test_challenge_publishes_empty_none_bundle_and_keeps_checkpoint(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"
    initial_fixture = write_feed_fixture(
        tmp_path / "initial.html",
        ["syn-challenge-old-1", "syn-challenge-old-2", "syn-challenge-old-3"],
    )
    initial = invoke_discover(
        tmp_path,
        run_id="23232323-2222-4333-8444-555555555555",
        fixture=initial_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr
    state_path = state_dir / "synthetic-thai-gpu-group.json"
    checkpoint_before = json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"]
    fixture = write_feed_fixture(
        tmp_path / "challenge.html", [], access="challenge_required", sort_result="unverified"
    )
    result = invoke_discover(
        tmp_path,
        run_id="ffffffff-2222-4333-8444-555555555555",
        fixture=fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    bundle = Path(report["bundle_path"])
    run = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    assert report["coverage"] == "none"
    assert report["stop_reason"] == "access_blocked"
    assert report["error_code"] == "challenge_required"
    assert run["error_code"] == "challenge_required"
    assert run["session_result"] == "unknown"
    assert list((bundle / "envelopes").iterdir()) == []
    assert list((bundle / "assets").iterdir()) == []
    assert json.loads(state_path.read_text(encoding="utf-8"))["checkpoint"] == checkpoint_before


def test_unavailable_grant_is_reported_with_v1_error_code(tmp_path: Path) -> None:
    fixture = write_feed_fixture(tmp_path / "unavailable.html", [], access="unavailable")
    result = invoke_discover(
        tmp_path,
        run_id="19191919-2222-4333-8444-555555555555",
        fixture=fixture,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    run = json.loads((Path(report["bundle_path"]) / "run.json").read_text(encoding="utf-8"))
    assert report["coverage"] == "none"
    assert report["stop_reason"] == "access_blocked"
    assert run["error_code"] == "grant_unavailable"


def test_scroll_budget_exhaustion_keeps_checkpoint(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"
    config = json.loads(GROUP_CONFIG.read_text(encoding="utf-8"))
    config.update({"page_size": 1, "scroll_budget": 0})
    config_path = tmp_path / "group.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fixture = write_feed_fixture(tmp_path / "budget.html", ["syn-budget-1", "syn-budget-2"])
    result = invoke_discover(
        tmp_path,
        run_id="12121212-2222-4333-8444-555555555555",
        fixture=fixture,
        group_config=config_path,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["coverage"] == "partial"
    assert report["stop_reason"] == "scroll_budget_exhausted"
    assert report["partial_reasons"] == ["budget_before_overlap"]
    state = json.loads((state_dir / "synthetic-thai-gpu-group.json").read_text(encoding="utf-8"))
    assert state["checkpoint"] is None


def test_http_429_pauses_without_invented_v1_fields_or_checkpoint_change(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    state_dir = tmp_path / "state"
    initial_fixture = write_feed_fixture(tmp_path / "initial.html", ["syn-rate-1", "syn-rate-2"])
    initial = invoke_discover(
        tmp_path,
        run_id="16161616-2222-4333-8444-555555555555",
        fixture=initial_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr
    state_path = state_dir / "synthetic-thai-gpu-group.json"
    before = state_path.read_bytes()
    limited_fixture = write_feed_fixture(tmp_path / "rate-limited.html", [], access="rate_limited")
    limited = invoke_discover(
        tmp_path,
        run_id="17171717-2222-4333-8444-555555555555",
        fixture=limited_fixture,
        inbox=inbox,
        state_dir=state_dir,
    )
    assert limited.returncode == 2
    report = json.loads(limited.stdout)
    assert report["paused"] is True
    assert report["checkpoint_unchanged"] is True
    assert "stop_reason" not in report
    assert "error_code" not in report
    assert state_path.read_bytes() == before
    assert len(list((inbox / "incoming").iterdir())) == 1


def test_bundle_is_hidden_until_atomic_rename(tmp_path: Path) -> None:
    from paiya_collector.inbox import publish_bundle, visible_ready_runs

    run_id = "13131313-2222-4333-8444-555555555555"
    inbox = tmp_path / "inbox"

    def stop_before_rename(_temporary: Path, _destination: Path) -> None:
        raise RuntimeError("synthetic process stop before rename")

    try:
        publish_bundle(
            inbox,
            {"run_id": run_id},
            [],
            {},
            before_rename=stop_before_rename,
        )
    except RuntimeError as exc:
        assert str(exc) == "synthetic process stop before rename"
    else:
        raise AssertionError("the injected stop did not occur")

    incoming = inbox / "incoming"
    assert visible_ready_runs(incoming) == []
    assert not (incoming / run_id).exists()
    temporary = incoming / f".{run_id}.tmp"
    assert (temporary / "READY").is_file()
    assert (temporary / "envelopes").is_dir()
    assert (temporary / "assets").is_dir()
    assert not (temporary / ".retention.json").exists()


def test_verified_latest_sort_keeps_feed_order_in_checkpoint(tmp_path: Path) -> None:
    ordered_keys = ["syn-newest", "syn-next-newest", "syn-oldest"]
    fixture = write_feed_fixture(
        tmp_path / "verified-latest.html",
        ordered_keys,
        sort_result="verified_latest",
    )
    state_dir = tmp_path / "state"
    result = invoke_discover(
        tmp_path,
        run_id="14141414-2222-4333-8444-555555555555",
        fixture=fixture,
        state_dir=state_dir,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["coverage"] == "complete"
    assert summary["stop_reason"] == "feed_end"
    state = json.loads((state_dir / "synthetic-thai-gpu-group.json").read_text(encoding="utf-8"))
    assert state["checkpoint"]["top_keys"] == ordered_keys


def test_prune_expired_cli_removes_only_expired_group_bundles_and_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paiya_collector.cli import main

    incoming = tmp_path / "inbox" / "incoming"
    expired = incoming / "12345678-2222-4333-8444-555555555555"
    expired.mkdir(parents=True)
    (expired / "envelopes").mkdir()
    (expired / "assets").mkdir()
    (expired / "run.json").write_text(
        json.dumps(
            {
                "run_id": expired.name,
                "group_key": "owner-group-01",
                "ended_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (expired / "READY").write_bytes(b"")
    expired_temporary = incoming / ".22345678-2222-4333-8444-555555555555.tmp"
    expired_temporary.mkdir()
    (expired_temporary / "run.json").write_text(
        json.dumps(
            {
                "run_id": "22345678-2222-4333-8444-555555555555",
                "group_key": "owner-group-01",
                "ended_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (expired_temporary / "assets").mkdir()
    (expired_temporary / "assets" / "abandoned.jpg").write_bytes(b"expired synthetic image")
    marker_temporary = incoming / ".32345678-2222-4333-8444-555555555555.tmp"
    marker_temporary.mkdir()
    (marker_temporary / ".retention.json").write_text(
        json.dumps(
            {
                "run_id": "32345678-2222-4333-8444-555555555555",
                "group_key": "owner-group-01",
                "created_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (marker_temporary / "assets").mkdir()
    (marker_temporary / "assets" / "orphaned.jpg").write_bytes(b"expired synthetic image")
    other_group = incoming / "87654321-2222-4333-8444-555555555555"
    other_group.mkdir()
    (other_group / "envelopes").mkdir()
    (other_group / "assets").mkdir()
    (other_group / "run.json").write_text(
        json.dumps(
            {
                "run_id": other_group.name,
                "group_key": "someone-elses-group",
                "ended_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (other_group / "READY").write_bytes(b"")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_file = state_dir / "owner-group-01.json"
    state_file.write_text(
        json.dumps(
            {
                "group_key": "owner-group-01",
                "checkpoint": {
                    "run_id": "32345678-2222-4333-8444-555555555555",
                    "top_keys": ["expired-post-key"],
                    "advanced_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                },
                "published_keys": ["expired-post-key"],
                "published_key_seen_at": {
                    "expired-post-key": (datetime.now(UTC) - timedelta(days=8)).isoformat()
                },
                "published_runs": {
                    "42345678-2222-4333-8444-555555555555": {
                        "published_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                        "report": {"bundle_path": "expired"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "prune-expired",
                "--inbox",
                str(tmp_path / "inbox"),
                "--state-dir",
                str(state_dir),
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert set(output["removed_runs"]) == {
        expired.name,
        "22345678-2222-4333-8444-555555555555",
        "32345678-2222-4333-8444-555555555555",
    }
    assert output["expired_state"] == {
        "expired_published_keys": 1,
        "expired_published_runs": 1,
        "expired_checkpoint_keys": 1,
        "checkpoint_preserved": True,
    }
    assert not expired.exists()
    assert not expired_temporary.exists()
    assert not marker_temporary.exists()
    assert other_group.exists()
    pruned_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert pruned_state["checkpoint"]["run_id"] == "32345678-2222-4333-8444-555555555555"
    assert pruned_state["checkpoint"]["top_keys"] == []
    assert pruned_state["published_keys"] == []
    assert pruned_state["published_runs"] == {}


def test_discover_browser_requires_explicit_manual_confirmation() -> None:
    from paiya_collector.cli import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "discover-browser",
                "--group-config",
                "group.json",
                "--profile-dir",
                "profile",
                "--inbox",
                "inbox",
                "--state-dir",
                "state",
            ]
        )
