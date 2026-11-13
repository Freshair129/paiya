from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit

from paiya_collector.html_adapter import FeedDocument, Node
from paiya_collector.state import ensure_no_reparse_components

APPROVED_GROUP_KEY = "owner-group-01"
APPROVED_SCOPES = frozenset({"capture", "read", "media"})
APPROVED_RETENTION_DAYS = 7
_REQUIRED_SELECTORS = (
    "post_card",
    "post_text",
    "permalink",
    "seller_name",
    "comment",
    "comment_text",
    "comment_author",
    "sort_indicator",
)


@dataclass(frozen=True)
class LiveGroupConfig:
    group_key: str
    grant_id: str
    group_url: str
    identity_scope: str
    adapter_version: str
    scopes: tuple[str, ...]
    retention_days: int
    page_size: int
    overlap: int
    scroll_budget: int
    time_budget_seconds: int
    selectors: dict[str, str]


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    body_text: str
    sort_label: str
    feed_end: bool
    posts: tuple[VisiblePost, ...]
    retry_after: str | None = None
    http_status: int | None = None


class BrowserPage(Protocol):
    def navigate(self, url: str) -> None: ...

    def snapshot(self, selectors: dict[str, str]) -> PageSnapshot: ...

    def scroll_one_step(self) -> None: ...


class RateLimitPause(RuntimeError):
    def __init__(
        self,
        retry_after_until: datetime | None,
        *,
        manual_resume_required: bool | None = None,
    ) -> None:
        super().__init__("HTTP 429; Retry-After must be honored; checkpoint is unchanged")
        self.retry_after_until = retry_after_until
        self.manual_resume_required = (
            retry_after_until is None if manual_resume_required is None else manual_resume_required
        )


@dataclass(frozen=True)
class VisibleComment:
    text: str
    author_name: str | None = None
    comment_key: str | None = None
    source_time: str | None = None


@dataclass(frozen=True)
class VisiblePost:
    permalink: str
    post_text: str
    seller_name: str | None = None
    comments: tuple[VisibleComment, ...] = ()
    image_bytes: bytes | None = None
    image_capture_failed: bool = False
    pinned: bool = False
    suggested: bool = False
    scroll_step: int = 0
    reported_comment_count: int | None = None


def load_group_config(path: Path, *, repository_root: Path) -> LiveGroupConfig:
    config_path = path.resolve()
    repo_root = repository_root.resolve()
    if config_path.is_relative_to(repo_root):
        raise ValueError("live group mapping must be stored outside the repository")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read local group configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("group configuration must be a JSON object")

    group_key = _required_text(raw, "group_key")
    if group_key != APPROVED_GROUP_KEY:
        raise ValueError(f"group_key must be the owner-approved key {APPROVED_GROUP_KEY}")

    grant_id = _required_text(raw, "grant_id")
    try:
        parsed_grant_id = uuid.UUID(grant_id)
    except ValueError as exc:
        raise ValueError("grant_id must be a UUID") from exc
    if str(parsed_grant_id) != grant_id.lower():
        raise ValueError("grant_id must use canonical UUID form")

    group_url = _required_text(raw, "group_url")
    parsed_url = urlsplit(group_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.port is not None
        or not re.fullmatch(r"/groups/[^/]+/?", parsed_url.path)
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("group_url must be a plain HTTPS Facebook group URL")

    grant = raw.get("grant")
    if not isinstance(grant, dict) or grant.get("active") is not True:
        raise ValueError("local grant must be declared active")
    scopes = grant.get("scopes")
    if not isinstance(scopes, list) or set(scopes) != APPROVED_SCOPES:
        raise ValueError("grant scope must be exactly capture, read, and media")
    retention_days = grant.get("retention_days")
    if retention_days != APPROVED_RETENTION_DAYS:
        raise ValueError("grant retention must be 7 days")

    identity_scope = _required_text(raw, "identity_scope")
    adapter_version = _required_text(raw, "adapter_version")
    selectors = raw.get("selectors")
    if not isinstance(selectors, dict):
        raise ValueError("selectors must be an object")
    normalized_selectors: dict[str, str] = {}
    for name in _REQUIRED_SELECTORS:
        value = selectors.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"selector {name} must be a non-empty CSS selector")
        normalized_selectors[name] = value.strip()
    for name in ("image", "feed_end", "pinned", "suggested"):
        value = selectors.get(name)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"selector {name} must be a non-empty CSS selector")
            normalized_selectors[name] = value.strip()

    page_size = _bounded_int(raw, "page_size", default=5, minimum=1, maximum=20)
    overlap = _bounded_int(raw, "overlap", default=3, minimum=1, maximum=10)
    scroll_budget = _bounded_int(raw, "scroll_budget", default=30, minimum=0, maximum=30)
    time_budget_seconds = _bounded_int(
        raw, "time_budget_seconds", default=120, minimum=0, maximum=120
    )

    return LiveGroupConfig(
        group_key=group_key,
        grant_id=grant_id,
        group_url=group_url,
        identity_scope=identity_scope,
        adapter_version=adapter_version,
        scopes=tuple(sorted(scopes)),
        retention_days=retention_days,
        page_size=page_size,
        overlap=overlap,
        scroll_budget=scroll_budget,
        time_budget_seconds=time_budget_seconds,
        selectors=normalized_selectors,
    )


def classify_page(url: str, body_text: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    title_text = body_text.casefold()
    if any(marker in path for marker in ("/checkpoint", "/challenge", "/security")):
        return "challenge_required"
    if "security check" in title_text or "ยืนยันตัวตน" in title_text:
        return "challenge_required"
    if "login" in path or parsed.hostname == "login.facebook.com":
        return "session_required"
    if "log in to facebook" in title_text or "เข้าสู่ระบบ facebook" in title_text:
        return "session_required"
    return "ok"


def _is_configured_group_url(current_url: str, configured_url: str) -> bool:
    allowed_hosts = {"facebook.com", "www.facebook.com", "m.facebook.com"}
    try:
        current = urlsplit(current_url)
        configured = urlsplit(configured_url)
        if (
            current.scheme != "https"
            or current.hostname not in allowed_hosts
            or current.username is not None
            or current.password is not None
            or current.port is not None
            or configured.scheme != "https"
            or configured.hostname not in allowed_hosts
        ):
            return False
        current_segments = [unquote(segment) for segment in current.path.split("/") if segment]
        configured_segments = [
            unquote(segment) for segment in configured.path.split("/") if segment
        ]
    except ValueError:
        return False
    return (
        len(current_segments) >= 2
        and len(configured_segments) == 2
        and current_segments[:2] == configured_segments
        and current_segments[0].casefold() == "groups"
    )


def parse_retry_after(value: str | None, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = int(text, 10)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return deadline.astimezone(UTC)
    if seconds < 0:
        return None
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return (current.astimezone(UTC) + timedelta(seconds=seconds)).replace(microsecond=0)


def source_post_key_from_permalink(permalink: str) -> str | None:
    try:
        parsed = urlsplit(permalink)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        return None

    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    candidate: str | None = None
    for marker in ("posts", "permalink"):
        if marker in segments:
            index = segments.index(marker)
            if index + 1 < len(segments):
                candidate = segments[index + 1]
                break
    if candidate is None:
        query = parse_qs(parsed.query)
        candidate = next(
            (values[0] for name in ("story_fbid", "fbid") if (values := query.get(name))),
            None,
        )
    if candidate is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", candidate):
        return None
    return f"fb-post-{candidate}"


def feed_from_visible_posts(
    posts: list[VisiblePost] | tuple[VisiblePost, ...],
    *,
    sort_label: str,
    access: str,
    session_result: str,
    feed_end: bool,
    scroll_step_seconds: int,
    forced_stop_reason: str | None = None,
) -> FeedDocument:
    if access not in {
        "ok",
        "unavailable",
        "permission_denied",
        "session_required",
        "challenge_required",
    }:
        raise ValueError(f"unsupported browser access state: {access}")
    if session_result not in {"ok", "required", "unknown"}:
        raise ValueError(f"unsupported session result: {session_result}")
    if forced_stop_reason not in {None, "time_budget_exhausted", "scroll_budget_exhausted"}:
        raise ValueError(f"unsupported browser stop condition: {forced_stop_reason}")

    normalized_sort = " ".join(sort_label.casefold().split())
    verified_labels = {
        "newest posts",
        "newest first",
        "most recent",
        "ใหม่ที่สุด",
        "โพสต์ใหม่ก่อน",
        "โพสต์ใหม่ล่าสุด",
    }
    sort_result = "verified_latest" if normalized_sort in verified_labels else "unverified"

    root = Node("main", {"data-testid": "feed"}, [])
    cards: list[Node] = []
    images: dict[str, bytes] = {}
    for index, post in enumerate(posts):
        post_key = source_post_key_from_permalink(post.permalink) or ""
        attrs = {
            "data-testid": "feed-card",
            "data-post-key": post_key,
            "data-permalink": post.permalink,
            "data-scroll-step": str(post.scroll_step),
            "data-pinned": str(post.pinned).lower(),
            "data-suggested": str(post.suggested).lower(),
        }
        if post.seller_name:
            attrs["data-seller-name"] = post.seller_name
        if post.reported_comment_count is not None:
            attrs["data-comment-count"] = str(post.reported_comment_count)

        children: list[Node | str] = [Node("div", {"data-role": "post-text"}, [post.post_text])]
        for comment in post.comments:
            author = comment.author_name.strip().casefold() if comment.author_name else ""
            seller = post.seller_name.strip().casefold() if post.seller_name else ""
            if not author or not seller:
                speaker_role = "unknown"
            else:
                speaker_role = "seller" if author == seller else "other"
            comment_attrs = {
                "data-comment-key": comment.comment_key or "",
                "data-speaker-role": speaker_role,
            }
            if comment.source_time:
                comment_attrs["data-source-time"] = comment.source_time
            children.append(
                Node(
                    "div",
                    comment_attrs,
                    [Node("span", {"data-role": "comment-text"}, [comment.text])],
                )
            )

        if post.image_bytes or post.image_capture_failed:
            image_key = f"browser-image-{index}.jpg"
            if post.image_bytes:
                images[image_key] = post.image_bytes
            children.append(Node("img", {"data-role": "post-image", "src": image_key}, []))

        card = Node("article", attrs, children)
        cards.append(card)
        root.children.append(card)

    return FeedDocument(
        root=root,
        cards=tuple(cards),
        access=access,
        session_result=session_result,
        sort_result=sort_result,
        feed_end=feed_end,
        scroll_step_seconds=scroll_step_seconds,
        image_bytes=images,
        forced_stop_reason=forced_stop_reason,
        allow_local_image_files=False,
    )


def capture_browser_feed(
    page: BrowserPage,
    *,
    config: LiveGroupConfig,
    previously_published: set[str],
    now: datetime,
    monotonic=time.monotonic,
) -> FeedDocument:
    started = monotonic()
    current = page.snapshot(config.selectors)
    _raise_for_rate_limit(current, now=now)
    access = classify_page(current.url, current.body_text)
    if access != "ok":
        session = "required" if access == "session_required" else "unknown"
        return feed_from_visible_posts(
            (),
            sort_label="",
            access=access,
            session_result=session,
            feed_end=True,
            scroll_step_seconds=0,
        )

    if not _is_configured_group_url(current.url, config.group_url):
        raise ValueError("visible browser left the configured group")

    collected: list[VisiblePost] = []
    observed_versions: dict[tuple[str, int], set[str]] = {}
    observed_keys: set[str] = set()
    overlap_streak = 0
    scroll_steps = 0
    initial_sort_label = current.sort_label
    sort_label = initial_sort_label

    while True:
        _raise_for_rate_limit(current, now=now)
        access = classify_page(current.url, current.body_text)
        if access != "ok":
            session = "required" if access == "session_required" else "unknown"
            return feed_from_visible_posts(
                (),
                sort_label="",
                access=access,
                session_result=session,
                feed_end=True,
                scroll_step_seconds=0,
            )
        if not _is_configured_group_url(current.url, config.group_url):
            raise ValueError("visible browser left the configured group")

        if current.sort_label != initial_sort_label:
            sort_label = ""

        occurrences: dict[str, int] = {}
        new_posts: list[VisiblePost] = []
        for post in current.posts:
            post_key = source_post_key_from_permalink(post.permalink)
            identity_base = post_key or post.permalink or f"unkeyed-{len(occurrences)}"
            occurrence = occurrences.get(identity_base, 0)
            occurrences[identity_base] = occurrence + 1
            identity = (identity_base, occurrence)
            signature = _visible_post_signature(post)
            versions = observed_versions.setdefault(identity, set())
            if signature in versions:
                continue
            versions.add(signature)
            captured = replace(post, scroll_step=scroll_steps)
            collected.append(captured)
            new_posts.append(captured)

        for post in new_posts:
            post_key = source_post_key_from_permalink(post.permalink)
            if post_key is None or post_key in observed_keys:
                continue
            observed_keys.add(post_key)
            if post.pinned or post.suggested:
                overlap_streak = 0
            elif post_key in previously_published:
                overlap_streak += 1
            else:
                overlap_streak = 0
            if overlap_streak >= config.overlap:
                return feed_from_visible_posts(
                    collected,
                    sort_label=sort_label,
                    access="ok",
                    session_result="ok",
                    feed_end=False,
                    scroll_step_seconds=1,
                )

        if current.feed_end:
            return feed_from_visible_posts(
                collected,
                sort_label=sort_label,
                access="ok",
                session_result="ok",
                feed_end=True,
                scroll_step_seconds=1,
            )
        if monotonic() - started >= config.time_budget_seconds:
            return feed_from_visible_posts(
                collected,
                sort_label=sort_label,
                access="ok",
                session_result="ok",
                feed_end=False,
                scroll_step_seconds=1,
                forced_stop_reason="time_budget_exhausted",
            )
        if scroll_steps >= config.scroll_budget:
            return feed_from_visible_posts(
                collected,
                sort_label=sort_label,
                access="ok",
                session_result="ok",
                feed_end=False,
                scroll_step_seconds=1,
                forced_stop_reason="scroll_budget_exhausted",
            )

        page.scroll_one_step()
        scroll_steps += 1
        current = page.snapshot(config.selectors)


def _raise_for_rate_limit(snapshot: PageSnapshot, *, now: datetime) -> None:
    if snapshot.http_status == 429:
        raise RateLimitPause(parse_retry_after(snapshot.retry_after, now=now))


def _visible_post_signature(post: VisiblePost) -> str:
    document = {
        "permalink": post.permalink,
        "post_text": post.post_text,
        "seller_name": post.seller_name,
        "image_capture_failed": post.image_capture_failed,
        "comments": [
            {
                "text": comment.text,
                "author_name": comment.author_name,
                "comment_key": comment.comment_key,
                "source_time": comment.source_time,
            }
            for comment in post.comments
        ],
        "pinned": post.pinned,
        "suggested": post.suggested,
        "reported_comment_count": post.reported_comment_count,
    }
    digest = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    if post.image_bytes:
        digest.update(hashlib.sha256(post.image_bytes).digest())
    return digest.hexdigest()


def prune_expired_bundles(
    inbox_root: Path,
    *,
    group_key: str,
    retention_days: int,
    now: datetime,
) -> tuple[str, ...]:
    if retention_days < 1 or retention_days > APPROVED_RETENTION_DAYS:
        raise ValueError("bundle retention must be between 1 and 7 days")
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    cutoff = current.astimezone(UTC) - timedelta(days=retention_days)
    ensure_no_reparse_components(inbox_root, label="inbox path")
    incoming = Path(os.path.abspath(inbox_root)) / "incoming"
    ensure_no_reparse_components(incoming, label="inbox path")
    if not incoming.is_dir():
        return ()

    removed: list[str] = []
    for run_dir in sorted(incoming.iterdir()):
        if _is_reparse_point(run_dir) or not run_dir.is_dir():
            continue
        is_temporary = run_dir.name.startswith(".") and run_dir.name.endswith(".tmp")
        raw_run_id = run_dir.name[1:-4] if is_temporary else run_dir.name
        try:
            run_id = str(uuid.UUID(raw_run_id))
        except ValueError:
            continue
        if run_id != raw_run_id.lower():
            continue
        run_path = run_dir / "run.json"
        if _is_reparse_point(run_path):
            continue
        if not is_temporary:
            ready = run_dir / "READY"
            envelopes = run_dir / "envelopes"
            assets = run_dir / "assets"
            if (
                _is_reparse_point(ready)
                or not ready.is_file()
                or not run_path.is_file()
                or not envelopes.is_dir()
                or not assets.is_dir()
            ):
                continue
        run: object = None
        if run_path.is_file():
            try:
                run = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if isinstance(run, dict):
            if run.get("run_id") != run_id or run.get("group_key") != group_key:
                continue
            captured_at = _parse_timestamp(run.get("ended_at"))
        else:
            captured_at = None
        if is_temporary and (captured_at is None or captured_at > current):
            marker_path = run_dir / ".retention.json"
            if _is_reparse_point(marker_path) or not marker_path.is_file():
                continue
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                not isinstance(marker, dict)
                or marker.get("run_id") != run_id
                or marker.get("group_key") != group_key
            ):
                continue
            captured_at = _parse_timestamp(marker.get("created_at"))
        elif not is_temporary and not isinstance(run, dict):
            continue
        if captured_at is None or captured_at > current or captured_at > cutoff:
            continue
        if _contains_reparse_point(run_dir):
            continue
        shutil.rmtree(run_dir)
        if run_id not in removed:
            removed.append(run_id)
    return tuple(removed)


def _contains_reparse_point(path: Path) -> bool:
    return any(_is_reparse_point(candidate) for candidate in path.rglob("*"))


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _required_text(value: dict[str, object], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"group configuration requires non-empty {name}")
    return result.strip()


def _bounded_int(
    config: dict[str, object],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
