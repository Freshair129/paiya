from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from paiya_collector.agent import EvidenceAgent, EvidenceClaims


class AdapterError(ValueError):
    pass


class CardParseError(AdapterError):
    pass


class UnsupportedSourceCondition(AdapterError):
    """A source condition that cannot be represented honestly by inbox v1."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str]

    def descendants(self, predicate: Callable[[Node], bool]) -> Iterator[Node]:
        for child in self.children:
            if isinstance(child, Node):
                if predicate(child):
                    yield child
                yield from child.descendants(predicate)

    def first(self, predicate: Callable[[Node], bool]) -> Node | None:
        return next(self.descendants(predicate), None)

    def text_content(self) -> str:
        parts: list[str] = []
        for child in self.children:
            parts.append(child.text_content() if isinstance(child, Node) else child)
        return "".join(parts)


class _TreeBuilder(HTMLParser):
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {}, [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, [])
        self.stack[-1].children.append(node)
        if node.tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, [])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


@dataclass(frozen=True)
class FeedDocument:
    root: Node
    cards: tuple[Node, ...]
    access: str
    session_result: str
    sort_result: str
    feed_end: bool
    scroll_step_seconds: int
    image_bytes: dict[str, bytes] = field(default_factory=dict)
    forced_stop_reason: str | None = None
    allow_local_image_files: bool = True


@dataclass(frozen=True)
class ParsedCard:
    source_post_key: str
    payload: dict[str, object]
    assets: dict[str, dict[str, object]]
    asset_bytes: dict[str, bytes]
    pinned: bool
    suggested: bool
    scroll_step: int


_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_AMOUNT = r"([0-9๐-๙][0-9๐-๙,]*(?:\.[0-9๐-๙]{1,2})?)\s*(?:บาท|฿)"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_feed(path: Path) -> FeedDocument:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterError(f"could not read fixture: {exc}") from exc
    parser = _TreeBuilder()
    parser.feed(source)
    feed = parser.root.first(lambda node: node.attrs.get("data-testid") == "feed")
    if feed is None:
        raise AdapterError("fixture does not contain a data-testid=feed element")

    access = feed.attrs.get("data-access", "ok")
    if access == "rate_limited":
        raise UnsupportedSourceCondition(
            "rate_limited is not an inbox v1 error_code; checkpoint was not changed"
        )
    if access not in {
        "ok",
        "unavailable",
        "permission_denied",
        "session_required",
        "challenge_required",
    }:
        raise AdapterError(f"fixture has unsupported data-access value: {access}")

    session = feed.attrs.get("data-session-result", "unknown")
    if session not in {"ok", "required", "unknown"}:
        raise AdapterError(f"fixture has unsupported data-session-result value: {session}")
    sort = feed.attrs.get("data-sort-result", "unverified")
    if sort not in {"verified_latest", "unverified"}:
        raise AdapterError(f"fixture has unsupported data-sort-result value: {sort}")

    try:
        step_seconds = int(feed.attrs.get("data-scroll-step-seconds", "5"))
    except ValueError as exc:
        raise AdapterError("data-scroll-step-seconds must be an integer") from exc
    if step_seconds < 0:
        raise AdapterError("data-scroll-step-seconds cannot be negative")

    cards = tuple(
        feed.descendants(
            lambda node: node.tag == "article" and node.attrs.get("data-testid") == "feed-card"
        )
    )
    return FeedDocument(
        root=feed,
        cards=cards,
        access=access,
        session_result=session,
        sort_result=sort,
        feed_end=feed.attrs.get("data-feed-end", "true").lower() == "true",
        scroll_step_seconds=step_seconds,
    )


def parse_card(
    node: Node,
    *,
    fixture_path: Path,
    group_key: str,
    identity_scope: str,
    observed_at: str,
    evidence_agent: EvidenceAgent | None = None,
    image_bytes: dict[str, bytes] | None = None,
    allow_local_image_files: bool = True,
) -> ParsedCard:
    post_key = node.attrs.get("data-post-key", "").strip()
    if not post_key:
        raise CardParseError("card has no stable post key")
    permalink = node.attrs.get("data-permalink", "").strip()
    if not permalink.startswith("https://"):
        raise CardParseError(f"post {post_key} has no HTTPS permalink")

    post_text_node = node.first(lambda child: child.attrs.get("data-role") == "post-text")
    post_text = post_text_node.text_content().strip() if post_text_node else ""
    claims = evidence_agent.extract(post_text) if evidence_agent is not None else EvidenceClaims()
    model_match = _extract_model(post_text)
    parsed_model, parsed_model_quote = model_match if model_match is not None else (None, None)
    agent_model = _verified_text_claim(
        claims.product_model, claims.product_model_quote, "product_model", post_text
    )
    if (
        parsed_model is not None
        and agent_model is not None
        and _model_key(parsed_model) != _model_key(agent_model[0])
    ):
        agent_model = None
    model = parsed_model or (agent_model[0] if agent_model else None)
    model_quote = parsed_model_quote or (agent_model[1] if agent_model else None)

    parsed_ask = _extract_amount(post_text, r"(?:ราคา(?:ขาย)?|ขายราคา)\s*" + _AMOUNT)
    ask = _merge_price_claim(
        post_text,
        parsed_ask,
        claims.ask_amount_thb,
        claims.ask_price_quote,
        r"(?:ราคา(?:ขาย)?|ขายราคา)",
    )
    parsed_shipping = _extract_amount(post_text, r"(?:ค่าส่ง|ค่า\s*ส่ง|ค่าจัดส่ง)\s*" + _AMOUNT)
    shipping = _merge_price_claim(
        post_text,
        parsed_shipping,
        claims.shipping_amount_thb,
        claims.shipping_price_quote,
        r"(?:ค่าส่ง|ค่า\s*ส่ง|ค่าจัดส่ง)",
    )

    condition = _positive_condition(post_text)
    agent_condition = _verified_condition_claim(
        claims.condition_text, claims.condition_quote, post_text
    )
    if condition is not None and agent_condition is not None and condition != agent_condition[0]:
        agent_condition = None
    condition = condition or (agent_condition[0] if agent_condition else None)

    evidence: list[dict[str, object]] = []
    assets: dict[str, dict[str, object]] = {}
    asset_bytes: dict[str, bytes] = {}
    post_quote = model_quote or (ask[0] if ask else post_text[:160])
    if post_quote:
        evidence.append(_evidence("e-post", "seller", "post_text", post_text, post_quote))

    item_evidence = ["e-post"] if post_quote else []
    if ask:
        evidence.append(_evidence("e-ask", "seller", "post_text", post_text, ask[0]))
        item_evidence.append("e-ask")
    image_node = node.first(
        lambda child: child.tag == "img" and child.attrs.get("data-role") == "post-image"
    )
    image_reason: str | None = None
    if image_node is not None:
        image_reason = (
            "synthetic_fixture_image_unavailable"
            if allow_local_image_files
            else "visible_image_capture_failed"
        )
        try:
            image_source = image_node.attrs.get("src", "")
            image_data = image_bytes.get(image_source) if image_bytes else None
            if image_data is None:
                if allow_local_image_files:
                    image_data = _read_local_image(fixture_path, image_source)
                else:
                    raise AdapterError("visible_image_capture_failed")
            width, height = _jpeg_dimensions(image_data)
            sha256 = hashlib.sha256(image_data).hexdigest()
            staging_key = f"image-{sha256[:20]}.jpg"
            assets["e-image"] = {
                "staging_key": staging_key,
                "sha256": sha256,
                "mime": "image/jpeg",
                "width": width,
                "height": height,
                "bytes": len(image_data),
            }
            asset_bytes[staging_key] = image_data
            evidence.append(
                {
                    "local_key": "e-image",
                    "role": "seller",
                    "source_span": None,
                    "media_state": "captured",
                    "media_reason": None,
                }
            )
            image_reason = None
            item_evidence.append("e-image")
        except (OSError, AdapterError) as exc:
            image_reason = str(exc)
            evidence.append(
                {
                    "local_key": "e-image",
                    "role": "seller",
                    "source_span": None,
                    "media_state": "capture_failed",
                    "media_reason": image_reason,
                }
            )

    items: list[dict[str, object]] = []
    if model or ask:
        items.append(
            {
                "source_item_key": _item_key(model) if model else None,
                "model": model,
                "condition_text": condition,
                "source_status_label": None,
                "price": _price("ask", "each", ask[1]) if ask else None,
                "evidence_keys": item_evidence,
            }
        )

    if shipping:
        evidence.append(_evidence("e-shipping", "seller", "post_text", post_text, shipping[0]))
        items.append(
            {
                "source_item_key": None,
                "model": None,
                "condition_text": None,
                "source_status_label": None,
                "price": _price("shipping", "unknown", shipping[1]),
                "evidence_keys": ["e-shipping"],
            }
        )

    seller_key = node.attrs.get("data-seller-key") or None
    seller_name = node.attrs.get("data-seller-name") or None
    seller: dict[str, object] | None = None
    if seller_key or seller_name:
        seller = {
            "identity_scope": identity_scope,
            "external_actor_key": seller_key,
            "display_name": seller_name,
        }

    comments: list[dict[str, object]] = []
    known_actors: set[str] = set()
    seen_comment_keys: set[str] = set()
    for comment_node in node.descendants(
        lambda child: child.attrs.get("data-comment-key") is not None
    ):
        comment_key = comment_node.attrs.get("data-comment-key") or None
        if comment_key and comment_key in seen_comment_keys:
            continue
        if comment_key:
            seen_comment_keys.add(comment_key)
        text_node = comment_node.first(lambda child: child.attrs.get("data-role") == "comment-text")
        comment_text = text_node.text_content().strip() if text_node else ""
        if not comment_text:
            continue
        actor_key = comment_node.attrs.get("data-actor-key") or None
        role_override = comment_node.attrs.get("data-speaker-role")
        if role_override in {"seller", "other", "unknown"}:
            role = role_override
            actor_key = None
        else:
            role = (
                "seller"
                if actor_key and actor_key == seller_key
                else "other"
                if actor_key
                else "unknown"
            )
            if actor_key:
                known_actors.add(actor_key)
        source_time = _source_time(comment_node.attrs.get("data-source-time"))
        comments.append(
            {
                "source_comment_key": comment_key,
                "parent_key": None,
                "role": role,
                "actor_key": actor_key,
                "text": comment_text,
                "source_time": source_time,
            }
        )
        evidence.append(
            _evidence(
                f"e-comment-{comment_key or len(comments)}",
                role,
                "comment_text",
                comment_text,
                comment_text,
                comment_key=comment_key,
            )
        )

    reported_count = _optional_count(node.attrs.get("data-comment-count"))
    if reported_count is None:
        comment_coverage = "partial" if comments else "none"
    else:
        comment_coverage = "complete" if len(comments) >= reported_count else "partial"
    payload: dict[str, object] = {
        "schema_version": 1,
        "group_key": group_key,
        "source_post_key": post_key,
        "permalink": permalink,
        "observed_at": observed_at,
        "access": "ok",
        "posted_at": _source_time(node.attrs.get("data-posted-at")),
        "seller": seller,
        "items": items,
        "activity": {
            "source_reported_comment_count": reported_count,
            "distinct_comments_seen": len(comments),
            "distinct_known_commenters_seen": len(known_actors),
            "coverage": comment_coverage,
        },
        "comments": comments,
        "evidence": evidence,
    }
    try:
        scroll_step = int(node.attrs.get("data-scroll-step", "0"))
    except ValueError as exc:
        raise CardParseError(f"post {post_key} has an invalid data-scroll-step") from exc
    if scroll_step < 0:
        raise CardParseError(f"post {post_key} has a negative data-scroll-step")

    return ParsedCard(
        source_post_key=post_key,
        payload=payload,
        assets=assets,
        asset_bytes=asset_bytes,
        pinned=node.attrs.get("data-pinned", "false").lower() == "true",
        suggested=node.attrs.get("data-suggested", "false").lower() == "true",
        scroll_step=scroll_step,
    )


def _evidence(
    local_key: str,
    role: str,
    field: str,
    source_text: str,
    quote: str,
    *,
    comment_key: str | None = None,
) -> dict[str, object]:
    start = source_text.find(quote)
    span: dict[str, object] = {"field": field, "quote": quote}
    if comment_key is not None:
        span["comment_key"] = comment_key
    if start >= 0:
        span["start"] = start
        span["end"] = start + len(quote)
    return {
        "local_key": local_key,
        "role": role,
        "source_span": span,
        "media_state": "not_requested",
        "media_reason": None,
    }


def _extract_model(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\bRTX\s*([0-9๐-๙]{3,4})(?:\s*([0-9๐-๙]{1,2})\s*GB)?\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    model = f"RTX {match.group(1).translate(_THAI_DIGITS)}"
    if match.group(2):
        model += f" {match.group(2).translate(_THAI_DIGITS)}GB"
    return model, match.group(0)


def _extract_amount(text: str, pattern: str) -> tuple[str, str] | None:
    match = re.search(pattern, text)
    if not match:
        return None
    raw_amount = match.group(1).translate(_THAI_DIGITS).replace(",", "")
    try:
        decimal_amount = Decimal(raw_amount)
        if not decimal_amount.is_finite() or decimal_amount < 0:
            raise InvalidOperation
        amount = f"{decimal_amount:.2f}"
    except InvalidOperation as exc:
        raise CardParseError(f"invalid price amount: {raw_amount}") from exc
    return match.group(0), amount


def _model_key(value: str) -> str:
    return re.sub(r"\s+", "", value.translate(_THAI_DIGITS)).casefold()


def _positive_condition(source_text: str) -> str | None:
    occurrences = list(re.finditer(re.escape("สภาพดี"), source_text))
    if len(occurrences) != 1:
        return None
    match = occurrences[0]
    prefix = source_text[max(0, match.start() - 8) : match.start()]
    suffix = source_text[match.end() : match.end() + 12]
    if re.search(r"(?:ไม่ค่อย|ไม่ใช่|ไม่)\s*$", prefix):
        return None
    if re.match(r"\s*(?:ไม่ค่อย|ไม่ใช่|ไม่|แต่)", suffix):
        return None
    return "สภาพดี"


def _verified_condition_claim(
    value: str | None, quote: str | None, source_text: str
) -> tuple[str, str] | None:
    if value != "สภาพดี" or quote != "สภาพดี" or _positive_condition(source_text) is None:
        return None
    return value, quote


def _verified_text_claim(
    value: str | None, quote: str | None, field: str, source_text: str
) -> tuple[str, str] | None:
    if value is None and quote is None:
        return None
    if value is None or quote is None or not value.strip() or not quote.strip():
        return None
    if quote not in source_text:
        return None
    if _model_key(value) not in _model_key(quote):
        return None
    return value.strip(), quote


def _merge_price_claim(
    source_text: str,
    parsed: tuple[str, str] | None,
    amount_value: str | None,
    quote_value: str | None,
    label_pattern: str,
) -> tuple[str, str] | None:
    if amount_value is None and quote_value is None:
        return parsed
    if (
        amount_value is None
        or quote_value is None
        or not amount_value.strip()
        or not quote_value.strip()
    ):
        return parsed
    if quote_value not in source_text:
        return parsed
    if not re.search(label_pattern, quote_value, re.IGNORECASE):
        return parsed
    quoted = _extract_amount(quote_value, _AMOUNT)
    if quoted is None:
        return parsed
    try:
        decimal_amount = Decimal(amount_value.translate(_THAI_DIGITS).replace(",", ""))
        if not decimal_amount.is_finite() or decimal_amount < 0:
            return parsed
        proposed_amount = f"{decimal_amount:.2f}"
    except InvalidOperation:
        return parsed
    if proposed_amount != quoted[1]:
        return parsed
    if parsed is not None and parsed[1] != proposed_amount:
        return parsed
    return parsed or quoted


def _item_key(model: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", model.lower())
    return "gpu-" + "-".join(tokens)


def _price(kind: str, unit: str, amount: str) -> dict[str, str]:
    return {"kind": kind, "unit": unit, "amount": amount, "currency": "THB"}


def _source_time(source_text: str | None) -> dict[str, object]:
    source = source_text.strip() if source_text else None
    normalized = source.translate(_THAI_DIGITS) if source else ""
    if re.search(r"\d+\s*(?:นาที|min)", normalized, re.IGNORECASE):
        precision = "minute"
    elif re.search(r"\d+\s*(?:ชั่วโมง|ชม\.?|ช\.ม\.?|hour|hr)", normalized, re.IGNORECASE):
        precision = "hour"
    elif re.search(r"\d+\s*(?:วัน|day)", normalized, re.IGNORECASE):
        precision = "day"
    else:
        precision = "unknown"
    return {"source_text": source, "value": None, "precision": precision}


def _optional_count(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    normalized = value.translate(_THAI_DIGITS).replace(",", "").strip()
    try:
        count = int(normalized)
    except ValueError as exc:
        raise CardParseError(f"invalid comment count: {value}") from exc
    if count < 0:
        raise CardParseError("comment count cannot be negative")
    return count


def _read_local_image(fixture_path: Path, source: str) -> bytes:
    if not source:
        raise AdapterError("image source is missing")
    parsed = urlsplit(source)
    posix_path = PurePosixPath(parsed.path)
    if parsed.scheme or parsed.netloc or posix_path.is_absolute() or ".." in posix_path.parts:
        raise AdapterError("fixture image must be a relative local path")
    root = fixture_path.resolve().parent
    image_path = (root / Path(*posix_path.parts)).resolve()
    try:
        image_path.relative_to(root)
    except ValueError as exc:
        raise AdapterError("fixture image path escapes the fixture directory") from exc
    return image_path.read_bytes()


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise AdapterError("fixture image is not a JPEG")
    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index + 3 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        index += segment_length
    raise AdapterError("JPEG dimensions could not be read")
