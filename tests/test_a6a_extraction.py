from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope

from paiya.extract.rules import (
    RuleResult,
    extract_condition,
    extract_model,
    extract_vram,
    extract_warranty,
    extract_warranty_state,
    extract_warranty_until,
    warranty_base_date,
)
from paiya.ingest.service import ingest_fixture
from paiya.store.fixture_scope import seed_fixture_scope
from paiya.store.tables import evidence, listing_items, tags


def test_geforce_without_family_uses_nvidia_family():
    assert extract_model("GeForce 3060") == RuleResult("rtx-3060")


def test_radeon_without_rx_leaves_standalone_model():
    assert extract_model("Radeon 6600") == RuleResult("rx-6600")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rtx3060", RuleResult("rtx-3060")),
        ("RTX 3060", RuleResult("rtx-3060")),
        ("3060ti", RuleResult("rtx-3060-ti")),
        ("GeForce RTX 4070 Ti Super", RuleResult("rtx-4070-ti-super")),
        ("RX 7900 XTX", RuleResult("rx-7900-xtx")),
        ("ＲＴＸ ３０６０", RuleResult("rtx-3060")),
        ("5500", None),
        ("3090 super", RuleResult(None, "unknown_model")),
        ("gtx 3060", RuleResult(None, "unknown_model")),
        ("geforce rx 6600", RuleResult(None, "unknown_model")),
        ("3060 / 3070", RuleResult(None, "ambiguous_model")),
        ("3060 3060", RuleResult("rtx-3060")),
        ("a3060", None),
        ("30601", None),
    ],
)
def test_models(text, expected):
    assert extract_model(text) == expected


@pytest.mark.parametrize("text,value", [("12GB", "12"), ("12 g", "12"), ("8กิ๊ก", "8"), ("8กิก", "8")])
def test_vram_valid(text, value):
    assert extract_vram(text, RuleResult("rtx-3060")) == RuleResult(value)


def test_vram_rejections_and_absence():
    model = RuleResult("rtx-3060")
    assert extract_vram("16GB", model) == RuleResult(None, "vram_not_valid_for_model")
    assert extract_vram("8GB 12GB", model) == RuleResult(None, "ambiguous_vram")
    assert extract_vram("8GB", None) == RuleResult(None, "model_unknown")
    assert extract_vram("3060", model) is None


@pytest.mark.parametrize("text", ["สภาพเหมือนใหม่", "like new", "มือสอง", "used"])
def test_used_phrases(text):
    assert extract_condition(text) == RuleResult("used")


@pytest.mark.parametrize("text", ["ของใหม่", "new", "ซีล", "ไม่เคยใช้งาน", "เก่าเก็บ"])
def test_new_and_unused_phrases(text):
    assert extract_condition(text) == RuleResult("new")
    assert extract_condition(text, warranty_none=True) == RuleResult("new_no_warranty")


def test_condition_ambiguity_and_absence():
    assert extract_condition("มือสอง ของใหม่") == RuleResult(None, "ambiguous_condition")
    assert extract_condition("ใช้งานได้") is None


@pytest.mark.parametrize("text", ["หมดประกัน", "no warranty", "out of warranty"])
def test_warranty_none_phrases(text):
    assert extract_warranty(text) == RuleResult("none")


def test_warranty_active_ambiguity_and_absence():
    assert extract_warranty("มีประกัน") == RuleResult("active")
    assert extract_warranty("มีประกัน ไม่มีประกัน") == RuleResult(None, "ambiguous_warranty")
    assert extract_warranty("ใช้งานได้") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("เหลือ 1 เดือน", RuleResult("2026-12")),
        ("เหลือ 120 เดือน", RuleResult("2036-11")),
        ("เหลือ 0 เดือน", RuleResult(None, "warranty_date_invalid")),
        ("เหลือ 121 เดือน", RuleResult(None, "warranty_date_invalid")),
        ("เหลือ 1 ปี", RuleResult("2027-11")),
        ("เหลือ 10 ปี", RuleResult("2036-11")),
        ("เหลือ 0 ปี", RuleResult(None, "warranty_date_invalid")),
        ("เหลือ 11 ปี", RuleResult(None, "warranty_date_invalid")),
        ("ถึง 03/2570", RuleResult("2027-03")),
        ("ถึง 3/27", RuleResult("2027-03")),
        ("ถึง 03/70", RuleResult("2027-03")),
        ("ถึง มี.ค. 2570", RuleResult("2027-03")),
        ("ถึง มีค 2570", RuleResult("2027-03")),
        ("ถึง มีนาคม 2027", RuleResult("2027-03")),
        ("ถึง 13/2027", RuleResult(None, "warranty_date_invalid")),
        ("ถึง 03/2027 ถึง 04/2027", RuleResult(None, "ambiguous_warranty_until")),
    ],
)
def test_warranty_until(text, expected):
    assert extract_warranty_until(text, date(2026, 11, 15)) == expected


def test_past_warranty_keeps_end_month():
    assert extract_warranty_state("ประกันถึง 01/2026", date(2026, 11, 15)) == (
        RuleResult("none"),
        RuleResult("2026-01"),
    )


@pytest.mark.parametrize("precision", ["exact", "minute", "hour", "day"])
def test_warranty_base_uses_posted_at(precision):
    observed = datetime(2026, 12, 1, tzinfo=UTC)
    assert warranty_base_date(
        {"precision": precision, "value": "2026-11-15T00:00:00Z"}, observed
    ) == date(2026, 11, 15)


def test_warranty_base_fallback_and_bangkok_boundary():
    before = datetime(2026, 11, 15, 16, 59, tzinfo=UTC)
    after = datetime(2026, 11, 15, 17, 0, tzinfo=UTC)
    assert warranty_base_date(None, before) == date(2026, 11, 15)
    assert warranty_base_date({"precision": "unknown", "value": None}, after) == date(2026, 11, 16)


def capture(scope, model, condition, quote, *, post="one", observed="2026-11-15T12:00:00Z"):
    doc = envelope(scope, post)
    payload = doc["payload"]
    payload["observed_at"] = observed
    payload["posted_at"] = {
        "source_text": "2026-11-15",
        "value": "2026-11-15T00:00:00Z",
        "precision": "exact",
    }
    payload["items"][0].update(model=model, condition_text=condition, source_status_label=None)
    payload["evidence"][0]["source_span"]["quote"] = quote
    return doc


def saved_tags(engine, oid):
    with engine.connect() as conn:
        return {
            row.key: (row.value, row.validation_result, row.reason, row.local_key)
            for row in conn.execute(
                select(
                    tags.c.key,
                    tags.c.value,
                    tags.c.validation_result,
                    tags.c.reason,
                    evidence.c.local_key,
                )
                .join(evidence, tags.c.evidence_id == evidence.c.id)
                .where(tags.c.observation_id == oid)
            )
        }


@pytest.mark.parametrize(
    ("model", "condition", "quote", "expected"),
    [
        (
            "RTX 3060 12GB",
            "ใช้งานได้",
            "RTX 3060 12GB ราคา 5,500 บาท",
            {
                "model_key": ("rtx-3060", "confirmed", None, "status"),
                "vram_gb": ("12", "confirmed", None, "status"),
            },
        ),
        (
            "3060",
            "มือสอง",
            "3060 มือสอง",
            {
                "model_key": ("rtx-3060", "confirmed", None, "status"),
                "condition": ("used", "confirmed", None, "status"),
            },
        ),
        (
            "RTX 3060 Ti 12GB",
            "",
            "RTX 3060 Ti 12GB",
            {
                "model_key": ("rtx-3060-ti", "confirmed", None, "status"),
                "vram_gb": (None, "rejected", "vram_not_valid_for_model", "status"),
            },
        ),
        (
            "3060 / 3070",
            "",
            "3060 / 3070",
            {"model_key": (None, "rejected", "ambiguous_model", "status")},
        ),
        (
            "RTX 3070 8GB",
            "",
            "การ์ดจอสภาพดี",
            {
                "model_key": ("rtx-3070", "rejected", "quote_mismatch", "status"),
                "vram_gb": ("8", "rejected", "quote_mismatch", "status"),
            },
        ),
        ("", "สภาพเหมือนใหม่", "สภาพเหมือนใหม่", {"condition": ("used", "confirmed", None, "status")}),
        (
            "",
            "มือ1 ประกันเหลือ 8 เดือน",
            "มือ1 ประกันเหลือ 8 เดือน",
            {
                "condition": ("new", "confirmed", None, "status"),
                "warranty": ("active", "confirmed", None, "status"),
                "warranty_until": ("2027-07", "confirmed", None, "status"),
            },
        ),
        (
            "",
            "ไม่เคยใช้งาน เก่าเก็บ หมดประกัน",
            "ไม่เคยใช้งาน เก่าเก็บ หมดประกัน",
            {
                "condition": ("new_no_warranty", "confirmed", None, "status"),
                "warranty": ("none", "confirmed", None, "status"),
            },
        ),
        ("", "ซีล ไม่ได้บอกประกัน", "ซีล", {"condition": ("new", "confirmed", None, "status")}),
        (
            "",
            "มือสอง ประกันถึง 03/2570",
            "มือสอง ประกันถึง 03/2570",
            {
                "condition": ("used", "confirmed", None, "status"),
                "warranty": ("active", "confirmed", None, "status"),
                "warranty_until": ("2027-03", "confirmed", None, "status"),
            },
        ),
        (
            "",
            "มือสอง ประกันถึง 01/2026",
            "มือสอง ประกันถึง 01/2026",
            {
                "condition": ("used", "confirmed", None, "status"),
                "warranty": ("none", "confirmed", None, "status"),
                "warranty_until": ("2026-01", "confirmed", None, "status"),
            },
        ),
        (
            "",
            "มีประกัน ไม่มีประกัน",
            "มีประกัน ไม่มีประกัน",
            {"warranty": (None, "rejected", "ambiguous_warranty", "status")},
        ),
    ],
)
def test_contract_examples_through_ingest(pg_engine, tmp_path, model, condition, quote, expected):
    scope = seed_fixture_scope(pg_engine, "synthetic-extraction")
    oid = ingest_fixture(pg_engine, scope, capture(scope, model, condition, quote), {}, tmp_path)
    assert saved_tags(pg_engine, oid) == expected


def test_first_matching_eligible_evidence_and_replay(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-tag-evidence")
    doc = capture(scope, "3060", "", "unrelated")
    doc["payload"]["evidence"].append(
        {
            "local_key": "matching",
            "role": "seller",
            "source_span": {"field": "post_text", "quote": "RTX 3060"},
            "media_state": "not_requested",
        }
    )
    doc["payload"]["items"][0]["evidence_keys"] = ["status", "matching"]
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert saved_tags(pg_engine, oid) == {"model_key": ("rtx-3060", "confirmed", None, "matching")}
    with pg_engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(tags)).scalar_one()
    assert ingest_fixture(pg_engine, scope, doc, {}, tmp_path) == oid
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(tags)).scalar_one() == before


def test_projection_retains_latest_and_couples_vram(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-tag-projection")
    for model, observed in [
        ("3060 12GB", "2026-11-15T12:00:00Z"),
        ("3070", "2026-11-17T12:00:00Z"),
        ("3060 8GB", "2026-11-16T12:00:00Z"),
        ("unrelated", "2026-11-18T12:00:00Z"),
    ]:
        ingest_fixture(
            pg_engine, scope, capture(scope, model, "", model, observed=observed), {}, tmp_path
        )
    with pg_engine.connect() as conn:
        item = conn.execute(select(listing_items.c.model_key, listing_items.c.vram_gb)).one()
    assert item == ("rtx-3070", None)


def test_tag_constraints_and_unique(pg_engine, tmp_path):
    from uuid import uuid4

    from sqlalchemy import insert, update
    from sqlalchemy.exc import IntegrityError

    scope = seed_fixture_scope(pg_engine, "synthetic-tag-constraints")
    oid = ingest_fixture(pg_engine, scope, capture(scope, "3060", "", "3060"), {}, tmp_path)
    with pg_engine.connect() as conn:
        original = dict(
            conn.execute(select(tags).where(tags.c.observation_id == oid)).mappings().one()
        )
    cases = [
        {"key": "bad"},
        {"method": "bad"},
        {"validation_result": "bad"},
        {"validation_result": "rejected", "reason": "bad"},
        {"value": None},
        {"reason": "quote_mismatch"},
        {},
    ]
    for changes in cases:
        row = dict(original, id=uuid4(), **changes)
        with pytest.raises(IntegrityError), pg_engine.begin() as conn:
            conn.execute(insert(tags).values(**row))
    with pytest.raises(IntegrityError), pg_engine.begin() as conn:
        conn.execute(update(listing_items).values(warranty="bad"))


def test_cli_rebuild_is_idempotent_and_preserves_tags(pg_engine, tmp_path, monkeypatch, capsys):
    import json

    from sqlalchemy import delete, update

    from paiya import cli

    scope = seed_fixture_scope(pg_engine, "synthetic-extract-rebuild")
    ingest_fixture(
        pg_engine, scope, capture(scope, "3060 12GB", "used", "3060 12GB used"), {}, tmp_path
    )
    with pg_engine.begin() as conn:
        conn.execute(delete(tags))
        conn.execute(update(listing_items).values(model_key=None, vram_gb=None, condition=None))
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    assert cli.main(["extract", "rebuild"]) == 0
    first = json.loads(capsys.readouterr().out)["result"]
    assert first == {"observations_scanned": 1, "tags_added": 3, "items_changed": 1}
    with pg_engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(tags)).scalar_one()
        assert conn.execute(
            select(listing_items.c.model_key, listing_items.c.vram_gb, listing_items.c.condition)
        ).one() == ("rtx-3060", 12, "used")
    assert cli.main(["extract", "rebuild"]) == 0
    second = json.loads(capsys.readouterr().out)["result"]
    assert second == {"observations_scanned": 1, "tags_added": 0, "items_changed": 0}
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(tags)).scalar_one() == before


@pytest.mark.parametrize(
    "case,reason",
    [
        ("role", "role_mismatch"),
        ("actor", "actor_mismatch"),
        ("seller", "seller_unknown"),
        ("other", "no_eligible_evidence"),
    ],
)
def test_comment_rejections_bind_first_evidence(pg_engine, tmp_path, case, reason):
    from test_a4a_status import scenario

    scope = seed_fixture_scope(pg_engine, "synthetic-extract-rejections")
    doc = scenario("03-seller-reserves-3060.json")
    payload = doc["payload"]
    payload["group_key"] = scope.group_key
    payload["items"] = payload["items"][:1]
    item = payload["items"][0]
    item.update(model="3060", condition_text="", source_status_label=None, price=None)
    item["evidence_keys"] = ["s03-buyer-text"]
    ev = payload["evidence"][0]
    ev["source_span"]["quote"] = "3060"
    payload["comments"][0]["text"] = "3060"
    if case == "role":
        ev["role"] = "seller"
        payload["seller"] = None
    elif case == "actor":
        ev["role"] = "other"
    elif case == "seller":
        payload["seller"] = None
    else:
        payload["comments"][0]["actor_key"] = payload["seller"]["external_actor_key"]
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert saved_tags(pg_engine, oid) == {
        "model_key": ("rtx-3060", "rejected", reason, "s03-buyer-text")
    }


def test_without_evidence_keys_has_no_tags(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-extract-no-keys")
    doc = capture(scope, "3060", "used", "3060 used")
    doc["payload"]["items"][0]["evidence_keys"] = []
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert saved_tags(pg_engine, oid) == {}


def test_warranty_projection_requires_matching_warranty(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-extract-warranty-projection")
    for condition, observed in [
        ("ประกันถึง 03/2027", "2026-11-15T12:00:00Z"),
        ("หมดประกัน", "2026-11-16T12:00:00Z"),
    ]:
        ingest_fixture(
            pg_engine,
            scope,
            capture(scope, "", condition, condition, observed=observed),
            {},
            tmp_path,
        )
    with pg_engine.connect() as conn:
        assert conn.execute(
            select(listing_items.c.warranty, listing_items.c.warranty_until)
        ).one() == ("none", None)


def test_extraction_api_filters_and_fields(pg_engine, tmp_path):
    from fastapi.testclient import TestClient

    from paiya.api.app import app
    from paiya.api.evidence import get_db_connection, require_fixture_scope
    from paiya.read.service import bangkok_now

    scope = seed_fixture_scope(pg_engine, "synthetic-extract-api")
    doc = capture(scope, "3060 12GB", "มือสอง ประกันถึง 03/2027", "3060 12GB มือสอง ประกันถึง 03/2027")
    doc["payload"]["items"][0]["source_status_label"] = "เปิดขาย"
    doc["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    doc["payload"]["evidence"][0]["source_span"]["quote"] += " เปิดขาย"
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    from paiya.store.tables import observations

    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[require_fixture_scope] = lambda: scope
    app.dependency_overrides[get_db_connection] = db
    app.dependency_overrides[bangkok_now] = lambda: datetime(2026, 11, 15, tzinfo=UTC)
    try:
        with TestClient(app) as client:
            params = {
                "model_key": "rtx-3060",
                "vram_gb": 12,
                "condition": "used",
                "warranty": "active",
                "sale_status": "open",
                "min_price": 5000,
                "max_price": 6000,
            }
            response = client.get("/v1/posts", params=params)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            item = response.json()["posts"][0]["items"][0]
            assert [
                item[k]
                for k in (
                    "model_key",
                    "vram_gb",
                    "condition",
                    "warranty",
                    "warranty_until",
                    "warranty_months_left",
                )
            ] == ["rtx-3060", 12, "used", "active", "2027-03", 4]
            detail = client.get(f"/v1/posts/{post_id}")
            detail_item = detail.json()["current_items"][0]
            for key in (
                "model_key",
                "vram_gb",
                "condition",
                "warranty",
                "warranty_until",
                "warranty_months_left",
            ):
                assert detail_item[key] == item[key]
            assert len(client.get("/v1/posts").json()["posts"]) == 1
            assert client.get("/v1/posts", params={**params, "vram_gb": 8}).json()["posts"] == []
            for key, value in [
                ("vram_gb", "0"),
                ("vram_gb", "100"),
                ("vram_gb", "abc"),
                ("condition", "old"),
                ("warranty", "maybe"),
            ]:
                assert client.get("/v1/posts", params={key: value}).status_code == 422
            for now, expected in [
                (datetime(2027, 3, 1, tzinfo=UTC), 0),
                (datetime(2027, 4, 1, tzinfo=UTC), 0),
            ]:
                app.dependency_overrides[bangkok_now] = lambda now=now: now
                assert (
                    client.get(f"/v1/posts/{post_id}").json()["current_items"][0][
                        "warranty_months_left"
                    ]
                    == expected
                )
    finally:
        app.dependency_overrides.clear()


def test_unavailable_observation_creates_no_tags(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-extract-unavailable")
    doc = capture(scope, "3060", "used", "3060 used")
    payload = doc["payload"]
    doc["job_kind"] = "revisit"
    doc["page_check"]["sort"] = "not_applicable"
    doc["payload"] = {
        key: payload[key]
        for key in ("schema_version", "group_key", "source_post_key", "permalink", "observed_at")
    }
    doc["payload"]["access"] = "unavailable"
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert saved_tags(pg_engine, oid) == {}


def test_equal_time_projection_uses_observation_id(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-extract-tie")
    results = []
    for model in ("3060", "3070"):
        oid = ingest_fixture(pg_engine, scope, capture(scope, model, "", model), {}, tmp_path)
        results.append((oid, "rtx-" + model))
    with pg_engine.connect() as conn:
        assert conn.execute(select(listing_items.c.model_key)).scalar_one() == max(results)[1]


def test_api_same_item_filters_and_grant_isolation(pg_engine, tmp_path):
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy import insert

    from paiya.api.app import app
    from paiya.api.evidence import get_db_connection, require_fixture_scope
    from paiya.store.fixture_scope import FixtureScope
    from paiya.store.tables import source_grants

    a = seed_fixture_scope(pg_engine, "synthetic-extract-isolation")
    b = FixtureScope(a.group_id, uuid4(), a.group_key)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=b.grant_id,
                group_id=b.group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref="synthetic-extract-second",
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                retention_days=30,
            )
        )
    first = capture(a, "3060 12GB", "used", "3060 12GB used")
    first["payload"]["items"].append(
        dict(
            first["payload"]["items"][0],
            source_item_key="item-2",
            model="3070 8GB",
            condition_text="ของใหม่",
        )
    )
    first["payload"]["evidence"].append(
        {
            "local_key": "second",
            "role": "seller",
            "source_span": {"field": "post_text", "quote": "3070 8GB ของใหม่"},
            "media_state": "not_requested",
        }
    )
    first["payload"]["items"][1]["evidence_keys"] = ["second"]
    ingest_fixture(pg_engine, a, first, {}, tmp_path)
    second = capture(b, "3070 8GB", "ของใหม่", "3070 8GB ของใหม่", observed="2026-11-16T12:00:00Z")

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            app.dependency_overrides[require_fixture_scope] = lambda: a
            assert (
                len(
                    client.get(
                        "/v1/posts", params={"model_key": "rtx-3060", "condition": "used"}
                    ).json()["posts"]
                )
                == 1
            )
            assert (
                client.get(
                    "/v1/posts", params={"model_key": "rtx-3060", "condition": "new"}
                ).json()["posts"]
                == []
            )
            items = client.get("/v1/posts").json()["posts"][0]["items"]
            assert all(item["warranty_months_left"] is None for item in items)
            ingest_fixture(pg_engine, b, second, {}, tmp_path)
            assert client.get(
                "/v1/posts", params={"model_key": "rtx-3070", "condition": "new"}
            ).json()["posts"]
            assert client.get("/v1/posts", params={"model_key": "rtx-3060"}).json()["posts"] == []
            app.dependency_overrides[require_fixture_scope] = lambda: b
            assert client.get("/v1/posts", params={"model_key": "rtx-3060"}).json()["posts"] == []
            assert (
                len(client.get("/v1/posts", params={"model_key": "rtx-3070"}).json()["posts"]) == 1
            )
    finally:
        app.dependency_overrides.clear()


def test_buyer_comment_never_confirms_model_or_vram(pg_engine, tmp_path):
    from test_a4a_status import scenario

    scope = seed_fixture_scope(pg_engine, "synthetic-extract-buyer")
    doc = scenario("03-seller-reserves-3060.json")
    payload = doc["payload"]
    payload["group_key"] = scope.group_key
    payload["items"] = payload["items"][:1]
    payload["items"][0].update(
        model="3070 8GB",
        condition_text="",
        price=None,
        source_status_label=None,
        evidence_keys=["s03-buyer-text"],
    )
    payload["comments"][0]["text"] = "3070 8GB"
    payload["evidence"][0]["role"] = "seller"
    payload["evidence"][0]["source_span"]["quote"] = "3070 8GB"
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert saved_tags(pg_engine, oid) == {
        "model_key": ("rtx-3070", "rejected", "role_mismatch", "s03-buyer-text"),
        "vram_gb": ("8", "rejected", "role_mismatch", "s03-buyer-text"),
    }
