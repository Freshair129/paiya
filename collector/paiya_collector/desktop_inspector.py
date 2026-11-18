"""Read-only, content-free browser readiness check in a separate Collector process."""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paiya_collector.browser_adapter import (
    CHALLENGE_TEXT_MARKERS,
    LOGIN_TEXT_MARKERS,
    MARKER_CASEFOLD_OVERRIDES,
    RateLimitPause,
    _matches_group_path,
    classify_page,
    load_group_config,
)
from paiya_collector.browser_runtime import _READ_BODY, _http_origin, attach_dedicated_browser
from paiya_collector.live import _check_rate_limit_pause, _write_rate_limit_pause, group_run_lock

# The only embedded contexts accepted on a group page. Anything else stops the round.
# Facebook's own sandbox origin, in a non-rendered <iframe> directly under the main document.
SANDBOX_FRAME_ORIGIN = "https://www.fbsbx.com"
_SANDBOX_FRAME_ORIGIN = ("https", "www.fbsbx.com", 443)
# A resource-less <object> of this Facebook-specific type; it creates no child frame.
INERT_OBJECT_TYPE = "nested/pressable"
_HIDDEN_IFRAME = "node => node.tagName === 'IFRAME' && node.getClientRects().length === 0"

# Read security text first; a bounded local preview lets the owner verify matches.
_INSPECT = f"""(args) => {{
    const body = ({_READ_BODY})(args);
    if (!body || body.truncated) return {{ready: false, reason: 'security_scan_incomplete'}};
    const overrides = {json.dumps(MARKER_CASEFOLD_OVERRIDES)};
    const folded = Array.from(body.text, char => overrides[char] ??
        (/^[A-Z]$/.test(char) ? char.toLowerCase() : char)).join('');
    const challenges = {json.dumps(CHALLENGE_TEXT_MARKERS)};
    const logins = {json.dumps(LOGIN_TEXT_MARKERS)};
    if (challenges.some(marker => folded.includes(marker)))
        return {{ready: false, reason: 'challenge_required'}};
    if (logins.some(marker => folded.includes(marker)))
        return {{ready: false, reason: 'session_required'}};
    const sandbox = {json.dumps(SANDBOX_FRAME_ORIGIN)};
    const allowedEmbed = node => {{
        const tag = node.tagName.toLowerCase();
        // Without data or src, an <object> loads nothing and opens no browsing context.
        if (tag === 'object') return node.getAttribute('type') === {json.dumps(INERT_OBJECT_TYPE)}
            && !node.hasAttribute('data') && !node.hasAttribute('src');
        if (tag !== 'iframe' || node.getClientRects().length) return false;
        try {{
            const src = node.getAttribute('src') || '';
            return new URL(src, document.baseURI).origin === sandbox;
        }} catch (error) {{ return false; }}
    }};
    const embedded = Array.from(document.querySelectorAll('iframe,frame,object,embed,fencedframe'));
    if (!embedded.every(allowedEmbed))
        return {{ready: false, reason: 'embedded_frame_unverified'}};
    const visible = node => Boolean(node && node.getClientRects().length &&
        getComputedStyle(node).visibility === 'visible');
    const cards = Array.from(document.querySelectorAll(args.selectors.post_card)).filter(visible);
    if (!cards.length) return {{ready: false, reason: 'no_visible_posts'}};
    const candidate = cards.find(card => ['post_text', 'permalink', 'seller_name'].every(
        name => visible(card.querySelector(args.selectors[name]))));
    const oneCard = Boolean(candidate);
    const comments = candidate ?
        Array.from(candidate.querySelectorAll(args.selectors.comment)).filter(visible) : [];
    const commentFields = comments.some(comment => ['comment_text', 'comment_author'].every(
        name => visible(comment.querySelector(args.selectors[name]))));
    const sort = visible(document.querySelector(args.selectors.sort_indicator));
    const image = !args.selectors.image ||
        Boolean(candidate && visible(candidate.querySelector(args.selectors.image)));
    const ready = Boolean(oneCard && commentFields && sort && image);
    if (!ready) return {{ready: false, reason: 'selectors_unverified'}};
    return {{ready: true, reason: 'ok', preview: {{
        post: (candidate.querySelector(args.selectors.post_text).innerText || '').slice(0, 300),
        seller: (candidate.querySelector(args.selectors.seller_name).innerText || '').slice(0, 100)
    }}}};
}}"""


def inspect_page(*, repository_root: Path, data_root: Path) -> dict[str, object]:
    config = load_group_config(data_root / "owner-group-01.json", repository_root=repository_root)
    state = data_root / "live-state"
    run_id = str(uuid4())
    with group_run_lock(state, config.group_key, run_id):
        _check_rate_limit_pause(
            state, config.group_key, now=datetime.now(UTC), clear_manual_pause=False
        )
        try:
            return _inspect_page(repository_root=repository_root, data_root=data_root)
        except RateLimitPause as pause:
            _write_rate_limit_pause(state, config.group_key, pause, run_id, datetime.now(UTC))
            raise


@contextmanager
def _checked_page(profile, *, config, repository_root):
    with attach_dedicated_browser(profile, config=config, repository_root=repository_root) as page:
        try:
            yield page
        finally:
            page.raise_if_rate_limited()


def _embedded_frame_reason(child_urls: list[str], group_url: str) -> str:
    """Classify stop reasons using URL metadata only."""
    origin = _http_origin(group_url)
    # Generic browser errors and foreign URLs cannot establish a Facebook challenge.
    access = {classify_page(url, "") for url in child_urls if _http_origin(url) == origin}
    if "challenge_required" in access:
        return "challenge_required"
    if "session_required" in access:
        return "session_required"
    if any(url.rstrip("/") == "chrome-error://chromewebdata" for url in child_urls):
        return "embedded_frame_load_error"
    return "embedded_frame_unverified"


def _only_hidden_sandbox_frames(page) -> bool:
    """Accept child frames only if each is the hidden Facebook sandbox iframe.

    Uses frame URLs and the owner element in the main document. Child documents are never
    read. Any doubt or browser error fails closed.
    """
    main = page._page.main_frame
    try:
        for frame in page._page.frames:
            if frame == main:
                continue
            if frame.parent_frame != main or _http_origin(frame.url) != _SANDBOX_FRAME_ORIGIN:
                return False
            if frame.frame_element().evaluate(_HIDDEN_IFRAME) is not True:
                return False
    except Exception:
        return False
    return True


def _inspect_page(*, repository_root: Path, data_root: Path) -> dict[str, object]:
    config = load_group_config(data_root / "owner-group-01.json", repository_root=repository_root)
    state = data_root / "live-state"
    _check_rate_limit_pause(
        state, config.group_key, now=datetime.now(UTC), clear_manual_pause=False
    )
    profile = data_root / "browser-profiles" / "owner-group-01"
    with _checked_page(profile, config=config, repository_root=repository_root) as page:
        page._guard_navigation()
        url = str(page._page.url)
        page._check_origin(url)
        access = classify_page(url, "")
        if access != "ok":
            return {"ready": False, "reason": access}
        if not _matches_group_path(url, config.group_url):
            return {"ready": False, "reason": "wrong_group"}
        # No child document is inspected, even when Chrome reports a failed load.
        frames = page._page.frames
        if len(frames) != 1 and not _only_hidden_sandbox_frames(page):
            child_urls = [frame.url for frame in frames if frame != page._page.main_frame]
            return {"ready": False, "reason": _embedded_frame_reason(child_urls, config.group_url)}
        revision = page._document_revision
        result = page._page.evaluate(_INSPECT, {"url": url, "selectors": config.selectors})
        page._check_snapshot(url, revision)
        if not isinstance(result, dict) or not isinstance(result.get("ready"), bool):
            return {"ready": False, "reason": "security_scan_incomplete"}
        if not result["ready"]:
            return {"ready": False, "reason": result.get("reason")}
        preview = result.get("preview")
        if not isinstance(preview, dict):
            return {"ready": False, "reason": "selectors_unverified"}
        post, seller = preview.get("post"), preview.get("seller")
        if (
            not isinstance(post, str)
            or not post.strip()
            or not isinstance(seller, str)
            or not seller.strip()
        ):
            return {"ready": False, "reason": "selectors_unverified"}
        return {
            "ready": True,
            "reason": "ok",
            "preview": {"post": post[:300], "seller": seller[:100]},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the dedicated group without collecting")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_page(repository_root=args.repository_root, data_root=args.data_root)
    except RateLimitPause as pause:
        deadline = pause.retry_after_until
        print(
            json.dumps(
                {
                    "reason": "http_429",
                    "retry_after_until": None if deadline is None else deadline.isoformat(),
                }
            )
        )
        return 2
    except Exception:
        # Do not put source URLs, browser state, credentials or exception details on stdout.
        result = {"ready": False, "reason": "inspection_unavailable"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
