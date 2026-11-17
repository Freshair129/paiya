"""Loopback-free browser fixtures for the desktop readiness check."""

from paiya_collector.desktop_inspector import _INSPECT
from playwright.sync_api import sync_playwright

SELECTORS = {
    "post_card": "article",
    "post_text": ".post-text",
    "permalink": ".permalink",
    "seller_name": ".seller",
    "comment": ".comment",
    "comment_text": ".comment-text",
    "comment_author": ".comment-author",
    "sort_indicator": ".sort",
    "image": "img",
}


def evaluate(html, selectors):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            return page.evaluate(_INSPECT, {"url": page.url, "selectors": selectors})
        finally:
            browser.close()


def test_readiness_reports_synthetic_matched_fields_without_source_content():
    result = evaluate(
        '<div class="sort">Newest</div><article><div class="post-text">Synthetic post</div>'
        '<a class="permalink" href="/post">Link</a><div class="seller">Synthetic seller</div>'
        '<img><div class="comment"><span class="comment-text">Hi</span>'
        '<span class="comment-author">Buyer</span></div></article>',
        SELECTORS,
    )
    assert result == {"ready": True, "reason": "ok"}


def test_challenge_stops_before_querying_card_fields():
    selectors = {**SELECTORS, "seller_name": "[invalid"}
    result = evaluate('<div role="dialog">Security check</div>', selectors)
    assert result == {"ready": False, "reason": "challenge_required"}


def test_missing_comment_fields_prevent_readiness():
    result = evaluate(
        '<div class="sort">Newest</div><article><div class="post-text">Item</div>'
        '<a class="permalink">Link</a><div class="seller">Seller</div><img></article>',
        SELECTORS,
    )
    assert result == {"ready": False, "reason": "selectors_unverified"}
