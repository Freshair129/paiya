"""M1a manual import acceptance tests.

The service and CLI stand in for the API in this package section. M1a has no
HTTP route or browser form; all examples and inputs here are synthetic.
"""

import base64
import hashlib
import ipaddress
import json
import os
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, update
from sqlalchemy.engine import make_url

from paiya.cli import main
from paiya.grants.errors import GrantError
from paiya.manual.service import ManualImportConflict, ManualImportService
from paiya.status.evidence import select_evidence_for_fact
from paiya.store.tables import (
    capture_runs,
    current_items,
    evidence,
    groups,
    jobs,
    listing_items,
    metadata,
    observations,
    posts,
    price_facts,
    sale_events,
    source_grants,
    status_facts,
)

ROOT = Path(__file__).resolve().parents[1]
WEBP = base64.b64decode("UklGRi4AAABXRUJQVlA4ICIAAABQAQCdASoCAAIAAkA4JQBOgCgAAP7tBywJK1ZulhhMGnAA")
PERMALINK = "https://www.facebook.com/groups/demo-group/posts/42/"
TWO_ITEM_TEXT = "RTX 3060 12GB 5,800 บาท\nRTX 3070 8GB 7,900 บาท\nค่าส่งรวม 150 บาท"
V1_EXPECTED_ROW_COUNTS = {
    "backup_runs": 0,
    "owner_sessions": 0,
    "groups": 1,
    "media_assets": 1,
    "purge_jobs": 0,
    "sellers": 5,
    "posts": 7,
    "source_grants": 1,
    "comments": 13,
    "jobs": 14,
    "listing_items": 10,
    "capture_gaps": 0,
    "capture_runs": 14,
    "watches": 0,
    "observations": 15,
    "comment_versions": 14,
    "evidence": 21,
    "price_facts": 18,
    "status_facts": 14,
    "tags": 36,
    "sale_events": 12,
    "current_items": 10,
}


@pytest.fixture
def pg_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql+psycopg://"):
        pytest.fail("PAIYA_TEST_DATABASE_URL must name a PostgreSQL 17 database")
    admin = create_engine(url)
    schema = f"paiya_test_{uuid4().hex}"
    with admin.begin() as conn:
        version = int(conn.exec_driver_sql("SHOW server_version_num").scalar_one())
        assert version // 10000 == 17
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    monkeypatch.setenv("PAIYA_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def manual_grant(pg_engine: Engine) -> UUID:
    group_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(UTC)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(groups).values(
                id=group_id,
                source="facebook",
                group_key="demo-group",
                canonical_url="https://www.facebook.com/groups/demo-group/",
            )
        )
        conn.execute(
            insert(source_grants).values(
                id=grant_id,
                group_id=group_id,
                source_kind="manual_share",
                scopes=["capture", "read"],
                authority_ref="synthetic-manual-grant",
                valid_from=now - timedelta(minutes=1),
                valid_until=None,
                retention_days=30,
            )
        )
    return grant_id


def _count(engine: Engine, table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _block_non_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def _install_network_spies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []
    original_connect = socket.socket.connect
    original_urlopen = urllib_request.urlopen

    def guarded_connect(sock: socket.socket, address) -> object:
        host = address[0] if isinstance(address, tuple) else str(address)
        if _block_non_loopback(host):
            attempts.append(host)
            raise AssertionError(f"non-loopback socket connection attempted: {host}")
        return original_connect(sock, address)

    def guarded_http_send(client: httpx.Client, request: httpx.Request, *args, **kwargs):
        host = request.url.host
        if _block_non_loopback(host):
            attempts.append(host)
            raise AssertionError(f"non-loopback HTTP request attempted: {host}")
        return original_http_send(client, request, *args, **kwargs)

    async def guarded_async_http_send(
        client: httpx.AsyncClient, request: httpx.Request, *args, **kwargs
    ):
        host = request.url.host
        if _block_non_loopback(host):
            attempts.append(host)
            raise AssertionError(f"non-loopback async HTTP request attempted: {host}")
        return await original_async_http_send(client, request, *args, **kwargs)

    def guarded_urlopen(url, *args, **kwargs):
        address = url.full_url if isinstance(url, urllib_request.Request) else str(url)
        host = urlsplit(address).hostname or ""
        if _block_non_loopback(host):
            attempts.append(host)
            raise AssertionError(f"non-loopback urllib request attempted: {host}")
        return original_urlopen(url, *args, **kwargs)

    original_http_send = httpx.Client.send
    original_async_http_send = httpx.AsyncClient.send
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(httpx.Client, "send", guarded_http_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async_http_send)
    monkeypatch.setattr(urllib_request, "urlopen", guarded_urlopen)
    return attempts


def test_preview_and_commit_make_zero_facebook_requests(
    pg_engine: Engine, manual_grant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _install_network_spies(monkeypatch)
    service = ManualImportService(pg_engine)
    preview = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    service.commit(preview, "network-zero", manual_grant)
    assert attempts == []


def test_preview_does_not_create_observation(pg_engine: Engine, manual_grant: UUID) -> None:
    preview = ManualImportService(pg_engine).preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    assert preview["source_kind"] == "manual_share"
    assert _count(pg_engine, observations) == 0
    assert _count(pg_engine, posts) == 0
    assert _count(pg_engine, jobs) == 0


def test_preview_rejects_a_revoked_grant(pg_engine: Engine, manual_grant: UUID) -> None:
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == manual_grant)
            .values(revoked_at=datetime.now(UTC))
        )
    with pytest.raises(GrantError) as error:
        ManualImportService(pg_engine).preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    assert error.value.code == "grant_unavailable"
    assert _count(pg_engine, observations) == 0


def test_confirm_creates_one_observation_with_item_price_evidence(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    service = ManualImportService(pg_engine)
    preview = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    result = service.commit(preview, "single-confirm", manual_grant)
    with pg_engine.connect() as conn:
        observation = (
            conn.execute(
                select(observations).where(observations.c.id == UUID(result["observation_id"]))
            )
            .mappings()
            .one()
        )
        run = (
            conn.execute(select(capture_runs).where(capture_runs.c.id == observation["run_id"]))
            .mappings()
            .one()
        )
        spans = (
            conn.execute(
                select(evidence.c.role, evidence.c.source_span).where(
                    evidence.c.observation_id == observation["id"]
                )
            )
            .mappings()
            .all()
        )
        prices = (
            conn.execute(
                select(price_facts).where(price_facts.c.observation_id == observation["id"])
            )
            .mappings()
            .all()
        )
    payload = observation["normalized_json"]["payload"]
    assert observation["post_id"] == UUID(result["post_id"])
    assert run["source_kind"] == "manual_share"
    assert run["session_result"] is None and run["sort_result"] is None
    assert payload["schema_version"] == 2
    assert payload["post_text"] == TWO_ITEM_TEXT
    assert len(payload["items"]) == 2
    assert len(prices) == 3
    assert all(row["role"] == "unknown" for row in spans)
    assert all(row["source_span"]["field"] == "post_text" for row in spans)
    assert len(result["item_ids"]) == 2


def test_retry_same_idempotency_key_saves_once(pg_engine: Engine, manual_grant: UUID) -> None:
    service = ManualImportService(pg_engine)
    preview = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    first = service.commit(preview, "same-body-key", manual_grant)
    second = service.commit(preview, "same-body-key", manual_grant)
    assert second == first
    assert _count(pg_engine, observations) == 1
    assert _count(pg_engine, jobs) == 1


def test_reused_idempotency_key_with_changed_body_returns_conflict(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    service = ManualImportService(pg_engine)
    first = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    changed = service.preview(TWO_ITEM_TEXT + " updated", PERMALINK, manual_grant)
    service.commit(first, "conflict-key", manual_grant)
    with pytest.raises(ManualImportConflict):
        service.commit(changed, "conflict-key", manual_grant)
    assert _count(pg_engine, observations) == 1


def test_commit_rejects_preview_changed_after_review(pg_engine: Engine, manual_grant: UUID) -> None:
    service = ManualImportService(pg_engine)
    preview = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    changed = {**preview, "post_text": TWO_ITEM_TEXT + " edited"}
    with pytest.raises(ValueError, match="Preview input no longer matches its digest"):
        service.commit(changed, "stale-preview", manual_grant)
    assert _count(pg_engine, observations) == 0


def test_new_idempotency_key_adds_observation_without_duplicate_events(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    service = ManualImportService(pg_engine)
    preview = service.preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    first = service.commit(preview, "new-round-1", manual_grant)
    second = service.commit(preview, "new-round-2", manual_grant)
    assert first["post_id"] == second["post_id"]
    assert first["observation_id"] != second["observation_id"]
    assert _count(pg_engine, observations) == 2
    assert _count(pg_engine, listing_items) == 2
    assert _count(pg_engine, status_facts) == 0
    assert _count(pg_engine, sale_events) == 0


def test_revoked_grant_after_preview_blocks_commit(pg_engine: Engine, manual_grant: UUID) -> None:
    preview = ManualImportService(pg_engine).preview(TWO_ITEM_TEXT, PERMALINK, manual_grant)
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == manual_grant)
            .values(revoked_at=datetime.now(UTC))
        )
    with pytest.raises(GrantError) as error:
        ManualImportService(pg_engine).commit(preview, "revoked-grant", manual_grant)
    assert error.value.code == "grant_unavailable"
    assert _count(pg_engine, observations) == 0
    assert _count(pg_engine, jobs) == 0


def test_v1_fixtures_validate_and_ingest_unchanged(pg_engine: Engine, tmp_path: Path) -> None:
    from paiya.ingest.service import ingest_fixture
    from paiya.store.fixture_scope import seed_fixture_scope

    scope = seed_fixture_scope(pg_engine, "synthetic-m1-v1-compat")
    fixture_paths = sorted((ROOT / "fixtures/scenarios").glob("*/rounds/*.json")) + sorted(
        (ROOT / "fixtures/contract/observation/valid").glob("*.json")
    )
    normalized_by_capture: dict[str, dict] = {}
    for path in fixture_paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"]["group_key"] = scope.group_key
        for asset in document["assets"].values():
            asset.update(
                staging_key="synthetic-m1-webp",
                sha256=hashlib.sha256(WEBP).hexdigest(),
                mime="image/webp",
                width=2,
                height=2,
                bytes=len(WEBP),
            )
        normalized_by_capture[document["capture_id"]] = document
        ingest_fixture(
            pg_engine,
            scope,
            document,
            {"synthetic-m1-webp": WEBP},
            tmp_path / "media",
        )
    with pg_engine.connect() as conn:
        stored = (
            conn.execute(
                select(
                    observations.c.capture_id,
                    observations.c.normalized_json,
                    observations.c.payload_hash,
                )
            )
            .mappings()
            .all()
        )
    assert len(stored) == len(fixture_paths)
    for row in stored:
        document = normalized_by_capture[row["capture_id"]]
        serialized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        assert row["normalized_json"] == document
        assert row["payload_hash"] == hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    assert _count(pg_engine, observations) == len(fixture_paths)
    assert _count(pg_engine, evidence) == sum(
        len(document["payload"].get("evidence", [])) for document in normalized_by_capture.values()
    )
    counts = {table.name: _count(pg_engine, table) for table in metadata.sorted_tables}
    assert counts == V1_EXPECTED_ROW_COUNTS


def test_manual_copy_keeps_author_unknown_and_does_not_confirm_reserved_or_sold(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    text = "RTX 3060 12GB 5,800 บาท จองครับ ขายแล้ว"
    service = ManualImportService(pg_engine)
    preview = service.preview(text, PERMALINK, manual_grant)
    service.commit(preview, "unknown-author", manual_grant)
    with pg_engine.connect() as conn:
        normalized = conn.execute(select(observations.c.normalized_json)).scalar_one()
        evidence_roles = conn.execute(select(evidence.c.role)).scalars().all()
    payload = normalized["payload"]
    assert payload.get("seller") is None
    assert all(role == "unknown" for role in evidence_roles)
    assert not payload.get("comments")
    assert _count(pg_engine, status_facts) == 0
    assert _count(pg_engine, sale_events) == 0
    assert _count(pg_engine, current_items) == 0


def test_deposit_decimal_is_not_misread_as_an_asking_price(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    preview = ManualImportService(pg_engine).preview(
        "RTX 3060 12GB มัดจำ 500.50 บาท", PERMALINK, manual_grant
    )
    facts = preview["items"][0]["price_facts"]
    assert [(fact["kind"], fact["amount"]) for fact in facts] == [("deposit", "500.50")]


def test_manual_price_range_keeps_both_bounds(pg_engine: Engine, manual_grant: UUID) -> None:
    service = ManualImportService(pg_engine)
    preview = service.preview("RTX 3060 12GB 5,000–6,000 บาท", PERMALINK, manual_grant)
    fact = preview["items"][0]["price_facts"][0]
    evidence_entry = next(
        entry for entry in preview["evidence"] if entry["local_key"] == fact["evidence_key"]
    )
    assert fact["amount"] == "5000.00"
    assert fact["amount_max"] == "6000.00"
    assert evidence_entry["source_span"]["quote"] == "RTX 3060 12GB 5,000–6,000 บาท"
    result = service.commit(preview, "stored-price-range", manual_grant)
    with pg_engine.connect() as conn:
        stored = conn.execute(
            select(price_facts.c.amount, price_facts.c.amount_max).where(
                price_facts.c.observation_id == UUID(result["observation_id"])
            )
        ).one()
    assert stored.amount == Decimal("5000.00")
    assert stored.amount_max == Decimal("6000.00")


def test_duplicate_model_item_ids_survive_reordering(pg_engine: Engine, manual_grant: UUID) -> None:
    service = ManualImportService(pg_engine)
    first_text = "RTX 3060 12GB black 5,000 บาท\nRTX 3060 12GB white 6,000 บาท"
    second_text = "RTX 3060 12GB white 6,000 บาท\nRTX 3060 12GB black 5,000 บาท"
    first_preview = service.preview(first_text, PERMALINK, manual_grant)
    second_preview = service.preview(second_text, PERMALINK, manual_grant)
    first = service.commit(first_preview, "duplicate-order-1", manual_grant)
    second = service.commit(second_preview, "duplicate-order-2", manual_grant)
    first_ids = dict(
        zip(
            (item["source_item_key"] for item in first_preview["items"]),
            first["item_ids"],
            strict=True,
        )
    )
    second_ids = dict(
        zip(
            (item["source_item_key"] for item in second_preview["items"]),
            second["item_ids"],
            strict=True,
        )
    )
    assert first_ids == second_ids
    with pg_engine.connect() as conn:
        rows = (
            conn.execute(
                select(
                    listing_items.c.source_item_key, listing_items.c.id, listing_items.c.resolution
                )
                .where(listing_items.c.post_id == UUID(first["post_id"]))
                .order_by(listing_items.c.source_item_key)
            )
            .mappings()
            .all()
        )
    assert {row["source_item_key"]: str(row["id"]) for row in rows} == first_ids
    assert all(row["resolution"] == "keyed" for row in rows)


def test_indistinguishable_duplicate_items_remain_unresolved(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    service = ManualImportService(pg_engine)
    text = "RTX 3060 12GB 5,000 บาท\nRTX 3060 12GB 5,000 บาท"
    preview = service.preview(text, PERMALINK, manual_grant)
    service.commit(preview, "duplicate-unresolved", manual_grant)
    assert len(set(item["source_item_key"] for item in preview["items"])) == 2
    with pg_engine.connect() as conn:
        resolutions = conn.execute(select(listing_items.c.resolution)).scalars().all()
    assert sorted(resolutions) == ["unresolved", "unresolved"]


def test_seller_evidence_with_buyer_comment_role_does_not_confirm_status() -> None:
    comment_id = uuid4()
    evidence_id = uuid4()
    decision = select_evidence_for_fact(
        ["e1"],
        {
            "e1": {
                "id": evidence_id,
                "role": "seller",
                "source_span": {"field": "comment_text", "comment_key": "c1"},
                "comment_version_id": comment_id,
            }
        },
        {comment_id: {"role": "other", "actor_key": "buyer-1", "source_time": None}},
        {"c1": "unknown"},
        "seller-1",
    )
    assert decision is not None
    assert decision[1] is False
    assert decision[2] == "role_mismatch"


def test_seller_evidence_with_mismatched_actor_key_does_not_confirm_status() -> None:
    comment_id = uuid4()
    evidence_id = uuid4()
    decision = select_evidence_for_fact(
        ["e1"],
        {
            "e1": {
                "id": evidence_id,
                "role": "seller",
                "source_span": {"field": "comment_text", "comment_key": "c1"},
                "comment_version_id": comment_id,
            }
        },
        {comment_id: {"role": "seller", "actor_key": "other-seller", "source_time": None}},
        {"c1": "exact"},
        "seller-1",
    )
    assert decision is not None
    assert decision[1] is False
    assert decision[2] == "actor_mismatch"


def test_cli_import_preview_emits_json(
    pg_engine: Engine,
    manual_grant: UUID,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paiya.cli.get_database_engine", lambda: pg_engine)
    text_file = tmp_path / "post.txt"
    text_file.write_text(TWO_ITEM_TEXT, encoding="utf-8")
    result = main(
        [
            "import",
            "preview",
            "--grant",
            str(manual_grant),
            "--permalink",
            PERMALINK,
            "--text-file",
            str(text_file),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["ok"] is True
    assert output["result"]["source_kind"] == "manual_share"


def test_cli_import_commit_emits_ids(
    pg_engine: Engine,
    manual_grant: UUID,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paiya.cli.get_database_engine", lambda: pg_engine)
    text_file = tmp_path / "post.txt"
    text_file.write_text(TWO_ITEM_TEXT, encoding="utf-8")
    preview_result = main(
        [
            "import",
            "preview",
            "--grant",
            str(manual_grant),
            "--permalink",
            PERMALINK,
            "--text-file",
            str(text_file),
        ]
    )
    preview_output = json.loads(capsys.readouterr().out)
    assert preview_result == 0
    preview_digest = preview_output["result"]["preview_digest"]
    result = main(
        [
            "import",
            "commit",
            "--grant",
            str(manual_grant),
            "--permalink",
            PERMALINK,
            "--text-file",
            str(text_file),
            "--idempotency-key",
            "cli-commit",
            "--preview-digest",
            preview_digest,
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["ok"] is True
    assert UUID(output["result"]["post_id"])
    assert UUID(output["result"]["observation_id"])
    assert len(output["result"]["item_ids"]) == 2


def test_cli_commit_rejects_text_changed_after_preview(
    pg_engine: Engine,
    manual_grant: UUID,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paiya.cli.get_database_engine", lambda: pg_engine)
    text_file = tmp_path / "post.txt"
    text_file.write_text(TWO_ITEM_TEXT, encoding="utf-8")
    assert (
        main(
            [
                "import",
                "preview",
                "--grant",
                str(manual_grant),
                "--permalink",
                PERMALINK,
                "--text-file",
                str(text_file),
            ]
        )
        == 0
    )
    preview_output = json.loads(capsys.readouterr().out)
    text_file.write_text(TWO_ITEM_TEXT + " edited", encoding="utf-8")
    result = main(
        [
            "import",
            "commit",
            "--grant",
            str(manual_grant),
            "--permalink",
            PERMALINK,
            "--text-file",
            str(text_file),
            "--idempotency-key",
            "stale-cli-preview",
            "--preview-digest",
            preview_output["result"]["preview_digest"],
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["ok"] is False
    assert output["error"]["code"] == "preview_changed"
    assert _count(pg_engine, observations) == 0


def test_cli_import_error_uses_json_error_format(
    pg_engine: Engine,
    manual_grant: UUID,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paiya.cli.get_database_engine", lambda: pg_engine)
    text_file = tmp_path / "post.txt"
    text_file.write_text(TWO_ITEM_TEXT, encoding="utf-8")
    result = main(
        [
            "import",
            "preview",
            "--grant",
            str(manual_grant),
            "--permalink",
            "https://m.facebook.com/share/p/short-link/",
            "--text-file",
            str(text_file),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["ok"] is False
    assert output["error"]["code"] == "invalid_permalink"


def test_permalink_parser_rejects_untrusted_hosts_locally(
    pg_engine: Engine, manual_grant: UUID
) -> None:
    service = ManualImportService(pg_engine)
    with pytest.raises(ValueError):
        service.preview(
            TWO_ITEM_TEXT,
            "https://facebook.com.evil.example/groups/demo-group/posts/42",
            manual_grant,
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("item_evidence", "Unassigned price fact uses item evidence"),
        ("reversed_range", "price amount_max is below amount"),
        ("missing_quote", "quote not found in post_text"),
        ("seller_role", "Invalid observation v2"),
        ("seller_nonnull", "Invalid observation v2"),
    ],
)
def test_v2_rejects_invalid_manual_claims(case: str, message: str) -> None:
    from paiya.ingest.validation import validate_observation

    path = ROOT / "fixtures/contract/observation/v2/valid/manual-multiple-facts.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    payload = document["payload"]
    if case == "item_evidence":
        payload["unassigned_price_facts"][0]["evidence_key"] = "ask"
    elif case == "reversed_range":
        payload["items"][0]["price_facts"][0]["amount_max"] = "5799.99"
    elif case == "missing_quote":
        payload["evidence"][0]["source_span"]["quote"] = "not in the copied text"
    elif case == "seller_role":
        payload["evidence"][0]["role"] = "seller"
    else:
        payload["seller"] = {"name": "claimed seller"}
    with pytest.raises(ValueError, match=message):
        validate_observation(document)


def test_v1_rejects_unknown_schema_version() -> None:
    from paiya.ingest.validation import validate_observation

    path = ROOT / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["schema_version"] = 0
    with pytest.raises(ValueError, match="Unsupported observation schema_version"):
        validate_observation(document)
