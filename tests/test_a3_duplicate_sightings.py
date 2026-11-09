"""Regression coverage for duplicate stable keys inside one discovery run."""

import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from paiya.collector import run as discovery
from paiya.collector.adapter import FeedCard, FeedRunData
from paiya.scheduler.models import JobClaim
from paiya.store.fixture_scope import FixtureScope


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalars(self):
        return []


class _ReadConnection:
    def __init__(self, group_id, grant_id):
        self.group_id = group_id
        self.grant_id = grant_id
        self.query_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _statement):
        self.query_count += 1
        if self.query_count == 1:
            return _Result({"group_id": self.group_id, "grant_id": self.grant_id})
        if self.query_count == 2:
            return _Result()
        raise AssertionError("discovery issued an unexpected read query")


class _WriteConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _statement):
        return _Result()


class _Engine:
    def __init__(self, group_id, grant_id):
        self.group_id = group_id
        self.grant_id = grant_id

    def connect(self):
        return _ReadConnection(self.group_id, self.grant_id)

    @contextmanager
    def begin(self):
        yield _WriteConnection()


def test_changed_duplicate_keeps_first_sighting_and_continues(monkeypatch, tmp_path, capsys):
    group_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(UTC)
    scope = FixtureScope(group_id, grant_id, "synthetic-duplicate-test")
    claim = JobClaim(
        job_id=uuid4(),
        group_id=group_id,
        grant_id=grant_id,
        run_id=uuid4(),
        kind="discover",
        logical_key="discover:synthetic-duplicate-test",
        target_key=scope.group_key,
        due_at=now,
        lease_token=uuid4(),
        lease_until=now + timedelta(minutes=5),
    )
    template_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))

    def card(key, amount):
        envelope = deepcopy(template)
        envelope["payload"]["group_key"] = scope.group_key
        envelope["payload"]["source_post_key"] = key
        envelope["payload"]["items"][0]["price"]["amount"] = str(amount)
        return FeedCard(key, False, False, "ok", envelope)

    feed = FeedRunData(
        "duplicates",
        now,
        "ok",
        "verified_latest",
        "ok",
        (
            (card("same-post", 5500),),
            (card("same-post", 6100), card("later-post", 4900)),
        ),
    )
    ingested = []
    monkeypatch.setattr(discovery, "ingest_fixture", lambda _e, _s, item, *_: ingested.append(item))
    monkeypatch.setattr(discovery, "verify_fixture_scope_for_commit", lambda *_: None)
    monkeypatch.setattr(discovery, "complete", lambda *_: None)
    monkeypatch.setattr(discovery, "maybe_fault", lambda *_: None)

    class Feed:
        def read_run(self, *_args):
            return feed

    result = discovery.execute_discovery(
        _Engine(group_id, grant_id),
        scope,
        claim,
        Feed(),
        "duplicates",
        now,
        media_root=tmp_path,
    )

    assert [
        (item["payload"]["source_post_key"], item["payload"]["items"][0]["price"]["amount"])
        for item in ingested
    ] == [("same-post", "5500"), ("later-post", "4900")]
    assert (result.cards_seen, result.cards_parsed) == (3, 3)
    assert result.duplicate_cards_skipped == 1
    assert result.ingested_keys == ("same-post", "later-post")
    assert result.scroll_steps == 1
    assert result.coverage == "complete" and result.checkpoint_advanced
    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "capture_run_finished"
    assert event["duplicate_cards_skipped"] == 1
    assert event["cards_seen"] == 3 and event["cards_parsed"] == 3
