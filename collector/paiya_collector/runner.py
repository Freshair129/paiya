from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from paiya_collector.agent import EvidenceAgent
from paiya_collector.html_adapter import (
    AdapterError,
    CardParseError,
    FeedDocument,
    ParsedCard,
    parse_card,
)


@dataclass(frozen=True)
class DiscoveryResult:
    parsed_cards: tuple[ParsedCard, ...]
    duplicate_skips: tuple[dict[str, object], ...]
    cards_seen: int
    cards_parsed: int
    scroll_steps: int
    stop_reason: str
    coverage: str
    partial_reasons: tuple[str, ...]
    session_result: str
    sort_result: str
    error_code: str | None
    checkpoint_top_keys: tuple[str, ...]


def run_discovery(
    feed: FeedDocument,
    *,
    fixture_path,
    group_key: str,
    identity_scope: str,
    observed_at: str,
    page_size: int,
    scroll_budget: int,
    time_budget_seconds: int,
    overlap: int,
    previously_published: set[str],
    evidence_agent: EvidenceAgent | None = None,
) -> DiscoveryResult:
    if feed.access != "ok":
        error_codes = {
            "unavailable": "grant_unavailable",
            "session_required": "session_required",
            "challenge_required": "challenge_required",
            "permission_denied": "permission_denied",
        }
        error_code = error_codes.get(feed.access)
        if error_code is None:
            raise AdapterError(
                f"access condition {feed.access} has no discovery v1 error mapping; run paused"
            )
        session_result = "required" if feed.access == "session_required" else feed.session_result
        if feed.access == "challenge_required" and session_result == "ok":
            session_result = "unknown"
        return DiscoveryResult(
            parsed_cards=(),
            duplicate_skips=(),
            cards_seen=0,
            cards_parsed=0,
            scroll_steps=0,
            stop_reason="access_blocked",
            coverage="none",
            partial_reasons=(),
            session_result=session_result,
            sort_result="unverified",
            error_code=error_code,
            checkpoint_top_keys=(),
        )

    if page_size < 1 or scroll_budget < 0 or time_budget_seconds < 0 or overlap < 1:
        raise ValueError("page_size/overlap must be positive and budgets cannot be negative")

    parsed_by_key: dict[str, ParsedCard] = {}
    parsed_order: list[ParsedCard] = []
    duplicate_skips: list[dict[str, object]] = []
    parse_failures = False
    cards_seen = 0
    cards_parsed = 0
    scroll_steps = 0
    elapsed_seconds = 0
    processed_indices: set[int] = set()
    overlap_streak = 0
    stop_reason: str | None = None
    checkpoint_candidates: list[str] = []

    while stop_reason is None:
        eligible = [
            index
            for index, card_node in enumerate(feed.cards)
            if index not in processed_indices
            and _scroll_step(card_node.attrs.get("data-scroll-step"), index, page_size)
            <= scroll_steps
        ]
        current_page = eligible[:page_size]
        for index in current_page:
            processed_indices.add(index)
            node = feed.cards[index]
            cards_seen += 1
            try:
                card = parse_card(
                    node,
                    fixture_path=fixture_path,
                    group_key=group_key,
                    identity_scope=identity_scope,
                    observed_at=observed_at,
                    evidence_agent=evidence_agent,
                    image_bytes=feed.image_bytes,
                    allow_local_image_files=feed.allow_local_image_files,
                )
            except CardParseError:
                parse_failures = True
                continue

            cards_parsed += 1
            prior = parsed_by_key.get(card.source_post_key)
            if prior is not None:
                duplicate_skips.append(
                    {
                        "source_post_key": card.source_post_key,
                        "content_changed": _card_signature(prior) != _card_signature(card),
                    }
                )
                continue

            parsed_by_key[card.source_post_key] = card
            parsed_order.append(card)
            if not card.pinned and not card.suggested:
                if len(checkpoint_candidates) < 20:
                    checkpoint_candidates.append(card.source_post_key)
                if card.source_post_key in previously_published:
                    overlap_streak += 1
                else:
                    overlap_streak = 0
                if overlap_streak >= overlap:
                    stop_reason = "overlap_reached"
                    break

        if stop_reason is not None:
            break
        if feed.forced_stop_reason is not None:
            stop_reason = feed.forced_stop_reason
            break
        if len(processed_indices) == len(feed.cards) and feed.feed_end:
            stop_reason = "feed_end"
            break
        if scroll_steps >= scroll_budget:
            stop_reason = "scroll_budget_exhausted"
            break
        if elapsed_seconds + feed.scroll_step_seconds > time_budget_seconds:
            stop_reason = "time_budget_exhausted"
            break
        scroll_steps += 1
        elapsed_seconds += feed.scroll_step_seconds

    if cards_seen == 0 or cards_parsed == 0:
        coverage = "none"
        reasons: list[str] = []
    else:
        reasons = []
        if feed.sort_result == "unverified":
            reasons.append("sort_unverified")
        if parse_failures:
            reasons.append("parse_failures")
        if stop_reason in {"scroll_budget_exhausted", "time_budget_exhausted"}:
            reasons.append("budget_before_overlap")
        if stop_reason == "feed_end" and previously_published:
            reasons.append("feed_stopped_before_overlap")
        coverage = "partial" if reasons else "complete"

    error_code = "adapter_error" if cards_parsed == 0 else None
    return DiscoveryResult(
        parsed_cards=tuple(parsed_order),
        duplicate_skips=tuple(duplicate_skips),
        cards_seen=cards_seen,
        cards_parsed=cards_parsed,
        scroll_steps=scroll_steps,
        stop_reason=stop_reason or "feed_end",
        coverage=coverage,
        partial_reasons=tuple(reasons),
        session_result=feed.session_result,
        sort_result=feed.sort_result,
        error_code=error_code,
        checkpoint_top_keys=tuple(checkpoint_candidates[:20]),
    )


def _scroll_step(value: str | None, index: int, page_size: int) -> int:
    if value is None:
        return index // page_size
    try:
        step = int(value)
    except ValueError as exc:
        raise AdapterError("data-scroll-step must be an integer") from exc
    if step < 0:
        raise AdapterError("data-scroll-step cannot be negative")
    return step


def _card_signature(card: ParsedCard) -> str:
    document = {"payload": card.payload, "assets": card.assets}
    content = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
