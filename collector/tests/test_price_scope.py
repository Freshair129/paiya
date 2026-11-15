"""Small regressions outside the B1a draft corpus."""

from pathlib import Path

import pytest
from paiya_collector.browser_adapter import VisiblePost, feed_from_visible_posts
from paiya_collector.html_adapter import parse_card


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("RTX 3060 12GB ราคา 500 บาท มัดจำ", None),
        ("RTX 3060 12GB ราคา ฿5,000 ถึง ฿5,500", None),
        ("RTX 3060 12GB ราคา 5,500 บาท กล่อง RTX 3060", ("RTX 3060 12GB", "5500.00")),
        ("RTX 3060 12GB ราคา 5,500 บาท มัดจำ 500 บาท", ("RTX 3060 12GB", "5500.00")),
    ],
)
def test_asking_price_uses_currency_and_unambiguous_item_evidence(text, expected):
    feed = feed_from_visible_posts(
        (VisiblePost("https://www.facebook.com/groups/synthetic/posts/99", text),),
        sort_label="newest posts",
        access="ok",
        session_result="ok",
        feed_end=True,
        scroll_step_seconds=0,
    )
    parsed = parse_card(
        feed.cards[0],
        fixture_path=Path("synthetic.html"),
        group_key="synthetic",
        identity_scope="synthetic",
        observed_at="2026-11-15T00:00:00Z",
    )
    asks = [
        (item["model"], item["price"]["amount"])
        for item in parsed.payload["items"]
        if item["price"] and item["price"]["kind"] == "ask" and item["price"]["unit"] == "each"
    ]
    assert asks == ([expected] if expected else [])
