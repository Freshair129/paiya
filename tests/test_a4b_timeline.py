import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4a_status import ingest

from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.collector.feed_script import FeedScriptAdapter
from paiya.collector.run import execute_discovery
from paiya.groups.service import add_group
from paiya.read.service import read_post, read_post_timeline
from paiya.scheduler.queue import claim_next, schedule_due
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import evidence, listing_items, observations, source_grants

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "fixtures/scenarios"
FEEDS = ROOT / "fixtures/feeds"
RUNS = [
    (p, run)
    for p in sorted(SCENARIOS.glob("S0[12]*/expected.json"))
    for run in json.loads(p.read_text(encoding="utf-8"))["runs"]
]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def post_id_for(engine, oid):
    with engine.connect() as conn:
        return conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()


def timeline(engine, scope, post_id):
    with engine.connect() as conn:
        return read_post_timeline(conn, scope, post_id, limit=100)["events"]


def variant(document, minute):
    result = copy.deepcopy(document)
    result["run_id"] = str(uuid4())
    result["capture_id"] = f"synthetic-{uuid4()}"
    result["payload"]["observed_at"] = f"2026-11-07T09:{minute:02d}:00Z"
    return result


@pytest.mark.parametrize(
    "expected_path,run", RUNS, ids=[f"{p.parent.name}-{r['name']}" for p, r in RUNS]
)
def test_status_timeline_matches_reviewed_history(pg_engine, tmp_path, expected_path, run):
    scope = seed_fixture_scope(pg_engine, "synthetic-timeline-status")
    ids = {}
    for filename in run["arrival_order"]:
        ids[filename] = ingest(
            pg_engine, scope, tmp_path, load(expected_path.parent / "rounds" / filename)
        )
    post_id = post_id_for(pg_engine, next(iter(ids.values())))
    events = timeline(pg_engine, scope, post_id)
    with pg_engine.connect() as conn:
        for key, item in run["expect"]["items"].items():
            item_id = conn.execute(
                select(listing_items.c.id).where(listing_items.c.source_item_key == key)
            ).scalar_one()
            actual = [
                e for e in events if e["kind"] == "status_changed" and e["item_id"] == item_id
            ]
            history = item["status_history"]
            assert [e["to"] for e in actual] == [h["status"] for h in history]
            assert [e["from"] for e in actual] == [None] + [h["status"] for h in history[:-1]]
            for event, wanted in zip(actual, history, strict=True):
                ref = wanted["evidence"]
                eid = conn.execute(
                    select(evidence.c.id).where(
                        evidence.c.observation_id == ids[ref["round"]],
                        evidence.c.local_key == ref["local_key"],
                    )
                ).scalar_one()
                assert event["evidence_id"] == eid
                assert event["observation_id"] == ids[ref["round"]]


def test_partial_comments_and_changes(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-comments")
    base = load(SCENARIOS / "S04-partial-comments/rounds/01-partial.json")
    oid = ingest(pg_engine, scope, tmp_path, base)
    post_id = post_id_for(pg_engine, oid)
    with pg_engine.connect() as conn:
        assert (
            read_post(conn, scope, post_id)["observations"][-1]["activity"]
            == load(SCENARIOS / "S04-partial-comments/expected.json")["runs"][0]["expect"][
                "activity"
            ]
        )
    events = timeline(pg_engine, scope, post_id)
    assert len([e for e in events if e["kind"] == "comment_added"]) == 2
    assert not [e for e in events if e["kind"] in ("comment_edited", "comment_count_changed")]
    second = variant(base, 10)
    second["payload"]["activity"]["source_reported_comment_count"] = 7
    ingest(pg_engine, scope, tmp_path, second)
    third = variant(second, 20)
    comment = copy.deepcopy(third["payload"]["comments"][0])
    comment["source_comment_key"] = "s04-new"
    third["payload"]["comments"].append(comment)
    third["payload"]["activity"].update(source_reported_comment_count=8, distinct_comments_seen=3)
    ingest(pg_engine, scope, tmp_path, third)
    fourth = variant(third, 30)
    ingest(pg_engine, scope, tmp_path, fourth)
    fifth = variant(fourth, 40)
    fifth["payload"]["comments"][-1]["text"] = "Synthetic edited comment"
    ingest(pg_engine, scope, tmp_path, fifth)
    unavailable = load(ROOT / "fixtures/contract/observation/valid/03-revisit-unavailable.json")
    unavailable["payload"].update(
        group_key=scope.group_key,
        source_post_key=base["payload"]["source_post_key"],
        permalink=base["payload"]["permalink"],
        observed_at="2026-11-07T09:50:00Z",
    )
    ingest(pg_engine, scope, tmp_path, unavailable)
    events = timeline(pg_engine, scope, post_id)
    counts = [e for e in events if e["kind"] == "comment_count_changed"]
    assert [(e["from"], e["to"]) for e in counts] == [(5, 7)]
    assert len([e for e in events if e["kind"] == "comment_added"]) == 3
    assert len([e for e in events if e["kind"] == "comment_edited"]) == 1
    assert [(e["from"], e["to"]) for e in events if e["kind"] == "access_changed"] == [
        ("ok", "unavailable")
    ]


def test_five_comments_one_known_author(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-five-comments")
    document = load(SCENARIOS / "S04-partial-comments/rounds/01-partial.json")
    comments = document["payload"]["comments"]
    for i in range(3, 6):
        item = copy.deepcopy(comments[0])
        item["source_comment_key"] = f"s04-c{i}"
        item["text"] = f"Synthetic comment {i}"
        comments.append(item)
    document["payload"]["activity"].update(
        distinct_comments_seen=5, distinct_known_commenters_seen=1, coverage="complete"
    )
    post_id = post_id_for(pg_engine, ingest(pg_engine, scope, tmp_path, document))
    with pg_engine.connect() as conn:
        activity = read_post(conn, scope, post_id)["observations"][0]["activity"]
    assert activity == {
        "source_reported_comment_count": 5,
        "distinct_comments_seen": 5,
        "distinct_known_commenters_seen": 1,
        "coverage": "complete",
    }
    assert (
        len([e for e in timeline(pg_engine, scope, post_id) if e["kind"] == "comment_added"]) == 5
    )


def test_price_changes_only_after_first_ask_per_item(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-price")
    first = load(SCENARIOS / "S03-multi-item/rounds/01-open-both.json")
    first["payload"]["items"][0]["price"]["amount"] = "6300"
    post_id = post_id_for(pg_engine, ingest(pg_engine, scope, tmp_path, first))
    assert not [e for e in timeline(pg_engine, scope, post_id) if e["kind"] == "price_changed"]
    second = variant(first, 10)
    second["payload"]["items"][0]["price"]["amount"] = "5800"
    oid = ingest(pg_engine, scope, tmp_path, second)
    events = [e for e in timeline(pg_engine, scope, post_id) if e["kind"] == "price_changed"]
    assert len(events) == 1
    with pg_engine.connect() as conn:
        item_id = conn.execute(
            select(listing_items.c.id).where(listing_items.c.source_item_key == "sku3060")
        ).scalar_one()
        eid = conn.execute(
            select(evidence.c.id).where(
                evidence.c.observation_id == oid, evidence.c.local_key == "s03-01-3060"
            )
        ).scalar_one()
    assert events[0]["item_id"] == item_id and events[0]["evidence_id"] == eid
    assert Decimal(events[0]["from"]) == 6300 and Decimal(events[0]["to"]) == 5800


@pytest.mark.parametrize(
    "directory", sorted(p for p in FEEDS.iterdir() if p.is_dir()), ids=lambda p: p.name
)
def test_labelled_feed_replay(pg_engine, tmp_path, directory):
    feed = load(directory / "feed.json")
    expected = load(directory / "expected.json")
    first_time = datetime.fromisoformat(feed["runs"][0]["started_at"].replace("Z", "+00:00"))
    with pg_engine.connect() as conn:
        group = add_group(conn, first_time - timedelta(minutes=1), feed["group_key"], 30)
        gid = UUID(group["id"])
        grant = conn.execute(
            select(source_grants.c.id).where(source_grants.c.group_id == gid)
        ).scalar_one()
    scope = FixtureScope(gid, grant, feed["group_key"])
    adapter = FeedScriptAdapter(directory / "feed.json")
    numerator = denominator = 0
    assert [r["label"] for r in feed["runs"]] == [r["label"] for r in expected["runs"]]
    for run, wanted in zip(feed["runs"], expected["runs"], strict=True):
        now = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
        with pg_engine.connect() as conn:
            schedule_due(conn, now)
            claim = claim_next(conn, now)
        assert claim is not None
        result = execute_discovery(
            pg_engine, scope, claim, adapter, run["label"], now, media_root=tmp_path
        )
        exp = wanted["expect"]
        for field in (
            "coverage",
            "stop_reason",
            "cards_seen",
            "cards_parsed",
            "checkpoint_advanced",
        ):
            assert getattr(result, field) == exp[field]
        assert list(result.partial_reasons) == exp["partial_reasons"]
        ingested = set(result.ingested_keys)
        assert ingested == set(exp["ingested_keys"])
        labelled = set(exp["labelled_visible_keys"])
        assert labelled and len(ingested & labelled) / len(labelled) >= 0.95
        numerator += len(ingested & labelled)
        denominator += len(labelled)
    assert numerator / denominator >= 0.95


def test_timeline_api_shape_pagination_and_authorization(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-timeline-api")
    directory = SCENARIOS / "S01-reserve-reopen/rounds"
    for path in sorted(directory.glob("*.json")):
        oid = ingest(pg_engine, scope, tmp_path, load(path))
    post_id = post_id_for(pg_engine, oid)
    later = variant(load(directory / "03-reopened.json"), 10)
    later["payload"]["observed_at"] = "2026-11-07T10:10:00Z"
    later["payload"]["activity"]["source_reported_comment_count"] = 2
    later["payload"]["activity"]["coverage"] = "partial"
    ingest(pg_engine, scope, tmp_path, later)
    new = variant(later, 20)
    new["payload"]["observed_at"] = "2026-11-07T10:20:00Z"
    new["payload"]["items"][0]["price"]["amount"] = "5000"
    comment = copy.deepcopy(new["payload"]["comments"][0])
    comment["source_comment_key"] = "synthetic-api-new"
    new["payload"]["comments"].append(comment)
    new["payload"]["activity"].update(source_reported_comment_count=3, distinct_comments_seen=2)
    ingest(pg_engine, scope, tmp_path, new)
    edited = variant(new, 30)
    edited["payload"]["observed_at"] = "2026-11-07T10:30:00Z"
    edited["payload"]["comments"][-1]["text"] = "Synthetic API edit"
    ingest(pg_engine, scope, tmp_path, edited)
    unavailable = load(ROOT / "fixtures/contract/observation/valid/03-revisit-unavailable.json")
    unavailable["payload"].update(
        group_key=scope.group_key,
        source_post_key=later["payload"]["source_post_key"],
        permalink=later["payload"]["permalink"],
        observed_at="2026-11-07T10:40:00Z",
    )
    ingest(pg_engine, scope, tmp_path, unavailable)
    path = f"/v1/posts/{post_id}/timeline"

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            assert client.get(path).status_code == 401
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            response = client.get(path)
            assert response.status_code == 200
            body = response.json()
            assert set(body) == {"post_id", "events", "next_cursor"}
            assert body["post_id"] == str(post_id) and body["next_cursor"] is None
            events = body["events"]
            assert events
            fields = {
                "status_changed": {"item_id", "from", "to", "evidence_id"},
                "price_changed": {"item_id", "from", "to", "evidence_id"},
                "comment_added": {"comment_id", "role"},
                "comment_edited": {"comment_id"},
                "comment_count_changed": {"from", "to"},
                "access_changed": {"from", "to"},
            }
            assert {event["kind"] for event in events} == set(fields)
            for event in events:
                assert set(event) == {"id", "kind", "at", "observation_id"} | fields[event["kind"]]
                assert event["at"].endswith("Z")
                assert datetime.fromisoformat(
                    event["at"].replace("Z", "+00:00")
                ).utcoffset() == timedelta(0)
                UUID(event["id"])
                UUID(event["observation_id"])
            assert events == sorted(
                events, key=lambda event: (event["at"], event["kind"], event["id"])
            )
            assert client.get(path).json()["events"] == events
            paged = []
            cursor = None
            while True:
                params = {"limit": 1}
                if cursor is not None:
                    params["cursor"] = cursor
                page = client.get(path, params=params).json()
                assert len(page["events"]) == 1
                paged.extend(page["events"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            assert paged == events
            assert len({e["id"] for e in paged}) == len(events)
            assert client.get(path, params={"limit": 101}).status_code == 422
            other = seed_fixture_scope(pg_engine, "synthetic-timeline-other")
            app.dependency_overrides[require_fixture_scope] = lambda: other
            response = client.get(path)
            assert response.status_code == 404 and response.json()["detail"]["code"] == "not_found"
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == scope.grant_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            assert client.get(path).status_code == 403
    finally:
        app.dependency_overrides.clear()
