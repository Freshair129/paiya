"""Loopback-free browser fixtures for the desktop readiness check."""

import pytest
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
            # Fixtures must never reach a network, even for synthetic embedded URLs.
            page.route("**/*", lambda route: route.abort())
            page.set_content(html)
            return page.evaluate(_INSPECT, {"url": page.url, "selectors": selectors})
        finally:
            browser.close()


def test_readiness_reports_synthetic_matched_fields_without_source_content():
    result = evaluate(
        '<div class="sort">Newest</div><article><div class="post-text">Synthetic post</div>'
        '<a class="permalink" href="/post">Link</a><div class="seller">Synthetic seller</div>'
        '<img style="width: 20px; height: 20px"><div class="comment">'
        '<span class="comment-text">Hi</span>'
        '<span class="comment-author">Buyer</span></div></article>',
        SELECTORS,
    )
    assert result == {
        "ready": True,
        "reason": "ok",
        "preview": {"post": "Synthetic post", "seller": "Synthetic seller"},
    }


def test_challenge_stops_before_querying_card_fields():
    selectors = {**SELECTORS, "seller_name": "[invalid"}
    result = evaluate('<div role="dialog">Security check</div>', selectors)
    assert result == {"ready": False, "reason": "challenge_required"}


def test_missing_comment_fields_prevent_readiness():
    result = evaluate(
        '<div class="sort">Newest</div><article><div class="post-text">Item</div>'
        '<a class="permalink">Link</a><div class="seller">Seller</div>'
        '<img style="width: 20px; height: 20px"></article>',
        SELECTORS,
    )
    assert result == {"ready": False, "reason": "selectors_unverified"}


def test_child_frame_is_rejected_before_card_selectors_are_queried():
    selectors = {**SELECTORS, "seller_name": "[invalid"}
    result = evaluate('<iframe srcdoc="&lt;p&gt;Synthetic&lt;/p&gt;"></iframe>', selectors)
    assert result == {"ready": False, "reason": "embedded_frame_unverified"}


CARD = (
    '<div class="sort">Newest</div><article><div class="post-text">Synthetic post</div>'
    '<a class="permalink" href="/post">Link</a><div class="seller">Synthetic seller</div>'
    '<img style="width: 20px; height: 20px"><div class="comment">'
    '<span class="comment-text">Hi</span><span class="comment-author">Buyer</span></div></article>'
)
READY = {
    "ready": True,
    "reason": "ok",
    "preview": {"post": "Synthetic post", "seller": "Synthetic seller"},
}
UNVERIFIED = {"ready": False, "reason": "embedded_frame_unverified"}
SANDBOX_IFRAME = '<iframe style="display: none" src="https://www.fbsbx.com/synthetic"></iframe>'
INERT_OBJECT = '<object type="nested/pressable" style="width: 40px; height: 19px"></object>'


def test_hidden_sandbox_iframe_and_inert_object_do_not_block_readiness():
    assert evaluate(SANDBOX_IFRAME + INERT_OBJECT + CARD, SELECTORS) == READY


@pytest.mark.parametrize(
    "embedded",
    [
        '<iframe src="https://www.fbsbx.com/synthetic" style="width: 30px; height: 30px"></iframe>',
        '<iframe style="display: none" src="http://www.fbsbx.com/synthetic"></iframe>',
        '<iframe style="display: none" src="https://fbsbx.com/synthetic"></iframe>',
        '<iframe style="display: none" src="https://www.fbsbx.com.example.invalid/x"></iframe>',
        '<iframe style="display: none" src="https://www.fbsbx.com@example.invalid/x"></iframe>',
        '<iframe style="display: none" src="https://www.fbsbx.com:8443/x"></iframe>',
        '<iframe style="display: none" src="https://example.invalid/x"></iframe>',
        '<iframe style="display: none"></iframe>',
        '<iframe style="display: none" srcdoc="&lt;p&gt;Synthetic&lt;/p&gt;"></iframe>',
        '<object type="text/html" style="width: 40px; height: 19px"></object>',
        '<object type="nested/pressable" data="https://www.fbsbx.com/x"></object>',
        '<object type="NESTED/PRESSABLE"></object>',
        "<object></object>",
        '<embed src="https://www.fbsbx.com/x">',
        '<fencedframe src="https://www.fbsbx.com/x"></fencedframe>',
    ],
)
def test_every_other_embedded_element_still_stops_the_round(embedded):
    selectors = {**SELECTORS, "seller_name": "[invalid"}
    assert evaluate(SANDBOX_IFRAME + embedded + CARD, selectors) == UNVERIFIED


def test_allowed_embeds_do_not_skip_the_challenge_scan():
    selectors = {**SELECTORS, "seller_name": "[invalid"}
    result = evaluate(
        SANDBOX_IFRAME + INERT_OBJECT + '<div role="dialog">Security check</div>', selectors
    )
    assert result == {"ready": False, "reason": "challenge_required"}
