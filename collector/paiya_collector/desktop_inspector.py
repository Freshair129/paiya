"""Read-only, content-free browser readiness check in a separate Collector process."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from paiya_collector.browser_adapter import (
    CHALLENGE_TEXT_MARKERS,
    LOGIN_TEXT_MARKERS,
    MARKER_CASEFOLD_OVERRIDES,
    RateLimitPause,
    _matches_group_path,
    classify_page,
    load_group_config,
)
from paiya_collector.browser_runtime import _READ_BODY, attach_dedicated_browser
from paiya_collector.live import _check_rate_limit_pause

# Read security text first and return only selector-presence flags.
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
    if (document.querySelector('iframe,frame,object,embed,fencedframe'))
        return {{ready: false, reason: 'embedded_frame_unverified'}};
    const visible = node => Boolean(node && node.getClientRects().length &&
        getComputedStyle(node).visibility === 'visible');
    const cards = Array.from(document.querySelectorAll(args.selectors.post_card)).filter(visible);
    if (!cards.length) return {{ready: false, reason: 'no_visible_posts'}};
    const oneCard = cards.some(card => ['post_text', 'permalink', 'seller_name'].every(
        name => visible(card.querySelector(args.selectors[name]))));
    const comments = cards.flatMap(card =>
        Array.from(card.querySelectorAll(args.selectors.comment))).filter(visible);
    const commentFields = comments.some(comment => ['comment_text', 'comment_author'].every(
        name => visible(comment.querySelector(args.selectors[name]))));
    const sort = visible(document.querySelector(args.selectors.sort_indicator));
    const image = !args.selectors.image ||
        cards.some(card => visible(card.querySelector(args.selectors.image)));
    return {{ready: Boolean(oneCard && commentFields && sort && image),
        reason: oneCard && commentFields && sort && image ? 'ok' : 'selectors_unverified'}};
}}"""


def inspect_page(*, repository_root: Path, data_root: Path) -> dict[str, object]:
    config = load_group_config(data_root / "owner-group-01.json", repository_root=repository_root)
    state = data_root / "live-state"
    _check_rate_limit_pause(
        state, config.group_key, now=datetime.now(UTC), clear_manual_pause=False
    )
    profile = data_root / "browser-profiles" / "owner-group-01"
    with attach_dedicated_browser(profile, config=config, repository_root=repository_root) as page:
        page._guard_navigation()
        url = str(page._page.url)
        page._check_origin(url)
        access = classify_page(url, "")
        if access != "ok":
            return {"ready": False, "reason": access}
        if not _matches_group_path(url, config.group_url):
            return {"ready": False, "reason": "wrong_group"}
        # The original Collector scans the main document only. A child frame
        # could contain a challenge that this preflight cannot safely classify.
        if len(page._page.frames) != 1:
            return {"ready": False, "reason": "embedded_frame_unverified"}
        revision = page._document_revision
        result = page._page.evaluate(_INSPECT, {"url": url, "selectors": config.selectors})
        page._check_snapshot(url, revision)
        page.raise_if_rate_limited()
        if not isinstance(result, dict) or not isinstance(result.get("ready"), bool):
            return {"ready": False, "reason": "security_scan_incomplete"}
        return {"ready": result["ready"], "reason": result.get("reason")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the dedicated group without collecting")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_page(repository_root=args.repository_root, data_root=args.data_root)
    except RateLimitPause:
        result = {"ready": False, "reason": "rate_limited"}
    except Exception:
        # Do not put source URLs, browser state, credentials or exception details on stdout.
        result = {"ready": False, "reason": "inspection_unavailable"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
