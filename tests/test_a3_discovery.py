from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401

from paiya.collector.adapter import FeedCard, FeedRunData
from paiya.collector.run import execute_discovery
from paiya.groups.service import add_group
from paiya.scheduler.queue import claim_next, schedule_due
from paiya.store.fixture_scope import FixtureScope
from paiya.store.tables import groups


class InlineFeed:
    def __init__(self, run):
        self.run = run

    def read_run(self, group_key, run_label, cards_per_step):
        return self.run


def test_feed_end_before_overlap_preserves_checkpoint(pg_engine, tmp_path: Path):
    now = datetime.now(UTC) - timedelta(minutes=1)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-feed-test", 30)
    from uuid import UUID

    gid = UUID(group["id"])
    with pg_engine.connect() as conn:
        from paiya.store.tables import source_grants

        grant = conn.execute(
            select(source_grants.c.id).where(source_grants.c.group_id == gid)
        ).scalar_one()
    scope = FixtureScope(gid, grant, "synthetic-feed-test")
    import json

    from test_a2_ingest import SAMPLE

    envelope = json.loads(SAMPLE.read_text(encoding="utf-8"))
    envelope["payload"]["group_key"] = scope.group_key
    envelope["payload"]["source_post_key"] = "post-one"
    card = FeedCard("post-one", False, False, "ok", envelope)
    feed = InlineFeed(FeedRunData("r", now, "ok", "verified_latest", "ok", ((card,),)))
    with pg_engine.connect() as conn:
        schedule_due(conn, now)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, now)
    first = execute_discovery(pg_engine, scope, claim, feed, "r", now, media_root=tmp_path)
    assert first.coverage == "complete"
    with pg_engine.connect() as conn:
        checkpoint = conn.execute(
            select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
        ).scalar_one()
    later = now + timedelta(minutes=30)
    with pg_engine.connect() as conn:
        schedule_due(conn, later)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, later)
    second = execute_discovery(pg_engine, scope, claim, feed, "r", later, media_root=tmp_path)
    assert second.stop_reason == "feed_end"
    assert second.coverage == "partial"
    assert second.partial_reasons == ("feed_stopped_before_overlap",)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
            ).scalar_one()
            == checkpoint
        )


def test_coverage_cases(pg_engine, tmp_path):
    import copy
    import json
    from uuid import UUID

    from sqlalchemy import func
    from test_a2_ingest import SAMPLE

    from paiya.collector.feed_script import FeedScriptAdapter
    from paiya.scheduler.settings import SchedulerSettings
    from paiya.store.tables import capture_runs, jobs, observations, source_grants

    now = datetime.now(UTC) - timedelta(minutes=1)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-cases", 30)
        gid = UUID(group["id"])
        grant = conn.execute(
            select(source_grants.c.id).where(source_grants.c.group_id == gid)
        ).scalar_one()
    scope = FixtureScope(gid, grant, "synthetic-cases")
    template = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def card(key, *, pinned=False, suggested=False):
        envelope = copy.deepcopy(template)
        envelope["payload"]["group_key"] = scope.group_key
        envelope["payload"]["source_post_key"] = key
        return FeedCard(key, pinned, suggested, "ok", envelope)

    def run(label, cards, *, sort="verified_latest", access="ok", settings=None):
        nonlocal now
        now += timedelta(minutes=30)
        root = tmp_path / label
        (root / "envelopes").mkdir(parents=True)
        raw_cards = []
        for index, item in enumerate(cards):
            path = f"envelopes/{index}.json" if item.envelope else None
            if path:
                (root / path).write_text(json.dumps(item.envelope), encoding="utf-8")
            raw_cards.append(
                {
                    "source_post_key": item.source_post_key,
                    "pinned": item.pinned,
                    "suggested": item.suggested,
                    "parse": item.parse,
                    "envelope": path,
                }
            )
        script = {
            "feed_version": 1,
            "group_key": scope.group_key,
            "runs": [
                {
                    "label": label,
                    "started_at": now.isoformat(),
                    "session": "ok",
                    "sort": sort,
                    "access": access,
                    "load_limit": None,
                    "cards": raw_cards,
                }
            ],
        }
        feed_path = root / "feed.json"
        feed_path.write_text(json.dumps(script), encoding="utf-8")
        feed = FeedScriptAdapter(feed_path)
        with pg_engine.connect() as conn:
            schedule_due(conn, now)
        with pg_engine.connect() as conn:
            claim = claim_next(conn, now)
        result = execute_discovery(
            pg_engine,
            scope,
            claim,
            feed,
            label,
            now,
            settings or SchedulerSettings(),
            media_root=tmp_path,
        )
        labelled_by_case = {
            "first": {"a", "b", "c", "pin", "suggest"} | {f"extra{i}" for i in range(22)},
            "reordered_pinned": {"new", "c", "pin", "a", "b"},
            "sort_unverified": {"a", "b", "c"},
            "feed_stopped": {"a"},
            "parse_failure": {"a", "b", "c", "new"},
            "budget": {f"x{i}" for i in range(10)},
            "blocked": set(),
        }
        labelled = labelled_by_case[label]
        actual = set(result.ingested_keys)
        if labelled:
            numerator = len(actual & labelled)
            print(f"AT-02 {label}: {numerator}/{len(labelled)} = {numerator / len(labelled):.3f}")
            assert numerator / len(labelled) >= 0.95
        else:
            print(f"AT-02 {label}: 0/0 (no readable labelled cards)")
        with pg_engine.connect() as conn:
            assert (
                conn.execute(
                    select(func.count()).select_from(jobs).where(jobs.c.id == claim.job_id)
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    select(func.count())
                    .select_from(capture_runs)
                    .where(capture_runs.c.id == claim.run_id)
                ).scalar_one()
                == 1
            )
            assert set(
                conn.execute(
                    select(observations.c.run_id).where(observations.c.run_id == claim.run_id)
                ).scalars()
            ) <= {claim.run_id}
        return result

    first = run(
        "first",
        [
            card("a"),
            card("b"),
            card("c"),
            card("pin", pinned=True),
            card("suggest", suggested=True),
            *[card(f"extra{i}") for i in range(22)],
        ],
    )
    assert first.coverage == "complete" and first.checkpoint_advanced
    with pg_engine.connect() as conn:
        checkpoint = conn.execute(
            select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
        ).scalar_one()
    assert checkpoint["top_keys"] == ["a", "b", "c"] + [f"extra{i}" for i in range(17)]
    reordered = run(
        "reordered_pinned", [card("new"), card("c"), card("pin", pinned=True), card("a"), card("b")]
    )
    assert reordered.stop_reason == "overlap_reached" and reordered.coverage == "complete"
    with pg_engine.connect() as conn:
        before_partial = conn.execute(
            select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
        ).scalar_one()
    unverified = run("sort_unverified", [card("a"), card("b"), card("c")], sort="unverified")
    assert unverified.partial_reasons == ("sort_unverified",) and not unverified.checkpoint_advanced
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
            ).scalar_one()
            == before_partial
        )
    stopped = run("feed_stopped", [card("a")])
    assert stopped.partial_reasons == ("feed_stopped_before_overlap",)
    failed = FeedCard(None, False, False, "failed", None)
    parsed = run("parse_failure", [card("a"), failed, card("b"), card("c"), card("new")])
    assert "parse_failures" in parsed.partial_reasons
    assert (parsed.cards_seen, parsed.cards_parsed) == (5, 4)
    budget = run(
        "budget",
        [card(f"x{i}") for i in range(11)],
        settings=SchedulerSettings(scroll_budget_steps=1),
    )
    assert budget.partial_reasons == ("budget_before_overlap",)
    blocked = run("blocked", [card("a")], access="permission_denied")
    assert blocked.coverage == "none" and blocked.error_code == "permission_denied"
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(jobs)).scalar_one() == 7
        assert conn.execute(select(func.count()).select_from(capture_runs)).scalar_one() == 7


@pytest.fixture
def overlap_feed(pg_engine, tmp_path):
    import copy
    import json
    from uuid import UUID

    from test_a2_ingest import SAMPLE

    from paiya.store.tables import source_grants

    now = datetime.now(UTC) - timedelta(minutes=1)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-overlap", 30)
        gid = UUID(group["id"])
        grant = conn.execute(
            select(source_grants.c.id).where(source_grants.c.group_id == gid)
        ).scalar_one()
    scope = FixtureScope(gid, grant, "synthetic-overlap")
    template = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def card(key, kind=None):
        envelope = copy.deepcopy(template)
        envelope["payload"]["group_key"] = scope.group_key
        envelope["payload"]["source_post_key"] = key
        return FeedCard(key, kind == "pinned", kind == "suggested", "ok", envelope)

    def run(batches):
        nonlocal now
        now += timedelta(minutes=30)
        feed = InlineFeed(FeedRunData("r", now, "ok", "verified_latest", "ok", batches))
        with pg_engine.connect() as conn:
            schedule_due(conn, now)
        with pg_engine.connect() as conn:
            claim = claim_next(conn, now)
        assert claim is not None
        return execute_discovery(pg_engine, scope, claim, feed, "r", now, media_root=tmp_path)

    return gid, card, run


@pytest.mark.parametrize("kind", ["pinned", "suggested"])
def test_known_excluded_cards_do_not_complete_overlap(pg_engine, overlap_feed, kind):
    from paiya.store.tables import posts

    gid, card, run = overlap_feed
    known = ["known-1", "known-2", "known-3"]
    first = run((tuple(card(key) for key in known),))
    assert first.coverage == "complete" and first.checkpoint_advanced
    with pg_engine.connect() as conn:
        checkpoint = conn.execute(
            select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
        ).scalar_one()

    new = ["new-1", "new-2"]
    second = run((tuple(card(key, kind) for key in known), tuple(card(key) for key in new)))
    assert second.stop_reason == "feed_end"
    assert second.coverage == "partial"
    assert second.partial_reasons == ("feed_stopped_before_overlap",)
    assert not second.checkpoint_advanced
    assert set(new) <= set(second.ingested_keys)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(groups.c.discovery_checkpoint).where(groups.c.id == gid)
            ).scalar_one()
            == checkpoint
        )
        assert set(
            conn.execute(select(posts.c.source_post_key).where(posts.c.group_id == gid)).scalars()
        ) >= set(new)


@pytest.mark.parametrize("kind", ["pinned", "suggested"])
def test_excluded_cards_do_not_break_overlap_run(overlap_feed, kind):
    _, card, run = overlap_feed
    first = run(((card("known-1"), card("known-2"), card("known-3")),))
    assert first.coverage == "complete"

    second = run(((card("known-1"), card("excluded-new", kind), card("known-2"), card("known-3")),))
    assert second.stop_reason == "overlap_reached"
    assert second.coverage == "complete"
    assert second.partial_reasons == ()
