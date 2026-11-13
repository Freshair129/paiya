from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from paiya_collector.agent import EvidenceClaims, OllamaError, OllamaEvidenceAgent
from paiya_collector.html_adapter import parse_card, parse_feed

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rtx3060.html"


def claims_document() -> dict[str, Any]:
    return {
        "product_model": "RTX 3060 12GB",
        "product_model_quote": "RTX 3060 12GB",
        "ask_amount_thb": "5500.00",
        "ask_price_quote": "ราคา 5,500 บาท",
        "shipping_amount_thb": "50.00",
        "shipping_price_quote": "ค่าส่ง 50 บาท",
        "condition_text": "สภาพดี",
        "condition_quote": "สภาพดี",
    }


class FakeResponse:
    status = 200
    reason = "OK"

    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class FakeConnection:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body
        self.request_args: tuple[object, ...] | None = None
        self.closed = False

    def request(self, *args: object, **kwargs: object) -> None:
        self.request_args = (*args, kwargs)

    def getresponse(self) -> FakeResponse:
        return FakeResponse(self.body)

    def close(self) -> None:
        self.closed = True


def test_ollama_agent_posts_only_one_post_to_fixed_loopback() -> None:
    connection = FakeConnection(
        {
            "message": {
                "role": "assistant",
                "content": json.dumps(claims_document(), ensure_ascii=False),
            }
        }
    )
    constructed: list[tuple[str, int, float]] = []

    def factory(host: str, port: int, timeout: float) -> FakeConnection:
        constructed.append((host, port, timeout))
        return connection

    agent = OllamaEvidenceAgent(connection_factory=factory)
    result = agent.extract("ขาย RTX 3060 12GB ราคา 5,500 บาท ค่าส่ง 50 บาท")

    assert result == EvidenceClaims(**claims_document())
    assert constructed == [("127.0.0.1", 11434, 180)]
    assert connection.closed
    assert connection.request_args is not None
    method, path, kwargs = connection.request_args
    payload = json.loads(kwargs["body"])  # type: ignore[arg-type]
    headers = kwargs["headers"]
    assert method == "POST"
    assert path == "/api/chat"
    assert headers == {"Content-Type": "application/json"}
    assert payload["model"] == "mellum:latest"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == 0
    assert payload["options"]["num_predict"] == 128
    assert payload["messages"][-1]["content"] == "ขาย RTX 3060 12GB ราคา 5,500 บาท ค่าส่ง 50 บาท"
    assert "cookie" not in json.dumps(payload, ensure_ascii=False).casefold()
    assert payload["format"]["type"] == "object"


def test_ollama_agent_rejects_invalid_model_response() -> None:
    connection = FakeConnection({"message": {"role": "assistant", "content": "not json"}})
    agent = OllamaEvidenceAgent(connection_factory=lambda *_: connection)

    with pytest.raises(OllamaError, match="valid JSON"):
        agent.extract("ข้อความโพสต์สังเคราะห์")


def test_parser_ignores_unmatched_agent_claims_and_keeps_deterministic_values() -> None:
    feed = parse_feed(FIXTURE)

    class HallucinatingAgent:
        def extract(self, post_text: str) -> EvidenceClaims:
            assert "RTX 3060" in post_text
            return EvidenceClaims(
                product_model="RTX 4090",
                product_model_quote="RTX 4090",
                ask_amount_thb="5500.00",
                ask_price_quote="ราคา 5,500 บาท",
                shipping_amount_thb="50.00",
                shipping_price_quote="ค่าส่ง 50 บาท",
                condition_text=None,
                condition_quote=None,
            )

    card = parse_card(
        feed.cards[0],
        fixture_path=FIXTURE,
        group_key="synthetic-thai-gpu-group",
        identity_scope="synthetic-thai-gpu-group",
        observed_at="2026-11-13T00:00:00Z",
        evidence_agent=HallucinatingAgent(),
    )
    product = next(item for item in card.payload["items"] if item["price"]["kind"] == "ask")
    assert product["model"] == "RTX 3060 12GB"
    assert product["price"]["amount"] == "5500.00"


def test_parser_supports_thai_digits_and_preserves_exact_evidence_quotes() -> None:
    feed = parse_feed(FIXTURE)
    post_text = feed.cards[0].first(lambda node: node.attrs.get("data-role") == "post-text")
    assert post_text is not None
    post_text.children = ["ขาย RTX ๓๐๖๐ ๑๒GB สภาพดี ราคา ๕,๕๐๐ บาท ค่าส่ง ๕๐ บาท"]

    card = parse_card(
        feed.cards[0],
        fixture_path=FIXTURE,
        group_key="synthetic-thai-gpu-group",
        identity_scope="synthetic-thai-gpu-group",
        observed_at="2026-11-13T00:00:00Z",
    )

    product = next(item for item in card.payload["items"] if item["price"]["kind"] == "ask")
    shipping = next(item for item in card.payload["items"] if item["price"]["kind"] == "shipping")
    assert product["model"] == "RTX 3060 12GB"
    assert product["price"]["amount"] == "5500.00"
    assert shipping["price"]["amount"] == "50.00"
    assert any(
        evidence["source_span"]["quote"] == "RTX ๓๐๖๐ ๑๒GB"
        for evidence in card.payload["evidence"]
        if evidence["source_span"] is not None
    )


def test_parser_rejects_positive_condition_from_a_negative_claim() -> None:
    feed = parse_feed(FIXTURE)
    post_text = feed.cards[0].first(lambda node: node.attrs.get("data-role") == "post-text")
    assert post_text is not None
    post_text.children = ["ขาย RTX 3060 ราคา 5,500 บาท สภาพไม่ดี"]

    class NegativeConditionAgent:
        def extract(self, source: str) -> EvidenceClaims:
            return EvidenceClaims(
                condition_text="ดี",
                condition_quote="สภาพไม่ดี",
            )

    card = parse_card(
        feed.cards[0],
        fixture_path=FIXTURE,
        group_key="synthetic-thai-gpu-group",
        identity_scope="synthetic-thai-gpu-group",
        observed_at="2026-11-13T00:00:00Z",
        evidence_agent=NegativeConditionAgent(),
    )
    product = next(item for item in card.payload["items"] if item["price"]["kind"] == "ask")
    assert product["condition_text"] is None


def test_parser_uses_grounded_agent_claims_and_cites_the_asking_price() -> None:
    feed = parse_feed(FIXTURE)

    class EvidenceAgent:
        def extract(self, post_text: str) -> EvidenceClaims:
            assert "ราคา 5,500 บาท" in post_text
            return EvidenceClaims(**claims_document())

    card = parse_card(
        feed.cards[0],
        fixture_path=FIXTURE,
        group_key="synthetic-thai-gpu-group",
        identity_scope="synthetic-thai-gpu-group",
        observed_at="2026-11-13T00:00:00Z",
        evidence_agent=EvidenceAgent(),
    )

    item = next(item for item in card.payload["items"] if item["price"]["kind"] == "ask")
    assert item["price"]["amount"] == "5500.00"
    assert any(
        evidence["source_span"]["quote"] == "ราคา 5,500 บาท"
        for evidence in card.payload["evidence"]
        if evidence["source_span"] is not None
    )
    assert "e-ask" in item["evidence_keys"]
