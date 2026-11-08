"""Real PostgreSQL 17 migration smoke test. Never silently skip this test."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope

from paiya.ingest.service import ingest_fixture
from paiya.store.fixture_scope import seed_fixture_scope

ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_and_downgrade_on_postgres17(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "PAIYA_TEST_DATABASE_URL must be set to a PostgreSQL 17 test database; "
            "database migration tests cannot be skipped"
        )
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail("PAIYA_TEST_DATABASE_URL must use postgresql+psycopg:// (no SQLite)")
    admin = create_engine(url)
    schema = f"paiya_migration_{uuid4().hex}"
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    try:
        with engine.connect() as connection:
            server_version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
        assert server_version // 10000 == 17, "Migration test requires PostgreSQL 17"
        monkeypatch.setenv("PAIYA_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
        alembic = Config(str(ROOT / "alembic.ini"))
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "0009_purge_jobs"
        command.downgrade(alembic, "0007_tags")
        command.upgrade(alembic, "head")
        command.downgrade(alembic, "0006_price_validation")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('tags')")).scalar_one() is None
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'listing_items' "
                        "AND column_name IN ('warranty', 'warranty_until')"
                    )
                ).scalar_one()
                == 0
            )
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('tags')")).scalar_one() == "tags"
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'listing_items' "
                        "AND column_name IN ('warranty', 'warranty_until')"
                    )
                ).scalar_one()
                == 2
            )
        command.downgrade(alembic, "0005_watches")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'price_facts' "
                        "AND column_name = 'validation_result'"
                    )
                ).scalar_one()
                == 0
            )
        command.upgrade(alembic, "head")
        command.downgrade(alembic, "0004_item_status")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004_item_status"
            )
            assert connection.execute(text("SELECT to_regclass('watches')")).scalar_one() is None
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('watches')")).scalar_one() == "watches"
            )
        scope = seed_fixture_scope(engine, "synthetic-migration")
        sample = ROOT / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
        envelope = json.loads(sample.read_text(encoding="utf-8"))
        envelope["payload"]["group_key"] = scope.group_key
        ingest_fixture(engine, scope, envelope, {}, tmp_path)
        with engine.connect() as connection:
            ingest_values = connection.execute(
                text("SELECT rule_version, validation_result, reason FROM price_facts")
            ).one()
        command.downgrade(alembic, "0005_watches")
        legacy_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO price_facts
                        (id, observation_id, post_id, item_id, evidence_id, fact_key,
                         price_kind, unit, amount, amount_max, currency)
                    SELECT :id, observation_id, post_id, item_id, evidence_id, 'legacy-copy',
                           price_kind, unit, amount, amount_max, currency
                    FROM price_facts LIMIT 1
                """),
                {"id": legacy_id},
            )
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT rule_version, validation_result, reason "
                        "FROM price_facts WHERE id = :id"
                    ),
                    {"id": legacy_id},
                ).one()
                == ingest_values
            )
        for sql in (
            "UPDATE price_facts SET validation_result = NULL WHERE id = :id",
            "UPDATE price_facts SET rule_version = NULL WHERE id = :id",
            "UPDATE price_facts SET validation_result = 'invalid' WHERE id = :id",
            "UPDATE price_facts SET reason = 'no_eligible_evidence' WHERE id = :id",
            "UPDATE price_facts SET validation_result = 'rejected', reason = NULL WHERE id = :id",
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(sql), {"id": legacy_id})
        command.downgrade(alembic, "0003_discovery_queue")
        command.upgrade(alembic, "head")
        command.downgrade(alembic, "0002_fixture_ingest")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM observations")).scalar_one() == 1
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT state FROM jobs")).scalar_one() == "succeeded"
            assert connection.execute(text("SELECT started_at FROM capture_runs")).scalar_one()
            assert connection.execute(text("SELECT count(*) FROM observations")).scalar_one() == 1
        command.downgrade(alembic, "base")
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT COUNT(*) FROM alembic_version")
            ).scalar_one()
        assert remaining == 0
        command.upgrade(alembic, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0009_purge_jobs"
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def test_downgrade_preserves_job_result(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-result-downgrade")
    ingest_fixture(pg_engine, scope, envelope(scope, "result"), {}, tmp_path)
    with pg_engine.begin() as connection:
        assert (
            connection.execute(text("UPDATE jobs SET result = '{\"processed\": 1}'")).rowcount == 1
        )

    with pytest.raises(RuntimeError, match=r"0 incompatible tags.*1.*result"):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "0007_tags")
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT result FROM jobs")).scalar_one() == {"processed": 1}
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0009_purge_jobs"
        )


def test_purge_jobs_downgrade_refuses_records_and_allows_empty(pg_engine):
    purge_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO purge_jobs
                    (id, scope_kind, scope_key, requested_at, state, result_counts)
                VALUES (:id, 'grant', 'synthetic-key', now(), 'succeeded', '{}'::jsonb)
            """),
            {"id": purge_id},
        )
    with pytest.raises(RuntimeError, match="purge_jobs contains 1"):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "0008_enrich_tags")
    with pg_engine.begin() as connection:
        assert connection.execute(text("SELECT id FROM purge_jobs")).scalar_one() == purge_id
        connection.execute(text("DELETE FROM purge_jobs WHERE id = :id"), {"id": purge_id})
    command.downgrade(Config(str(ROOT / "alembic.ini")), "0008_enrich_tags")
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('purge_jobs')")).scalar_one() is None


def test_downgrade_allows_null_job_results(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-null-result-downgrade")
    ingest_fixture(pg_engine, scope, envelope(scope, "null-result"), {}, tmp_path)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT result FROM jobs")).scalar_one() is None

    command.downgrade(Config(str(ROOT / "alembic.ini")), "0007_tags")
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0007_tags"
        )


def test_downgrade_preserves_model_tags(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-downgrade")
    doc = envelope(scope, "model-tag")
    doc["payload"]["items"][0]["model"] = "RTX 3060"
    doc["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060"
    ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    with pg_engine.begin() as connection:
        count = connection.execute(
            text("""
            UPDATE tags SET method = 'model', model_version = 'synthetic', confidence = 0.9
            WHERE key = 'model_key'
        """)
        ).rowcount
        assert count == 1
    with pytest.raises(
        RuntimeError, match=r"1 incompatible tags.*0 jobs with result.*deliberately"
    ):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "0007_tags")
    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM tags WHERE method = 'model'")
            ).scalar_one()
            == 1
        )
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0009_purge_jobs"
        )
    with pg_engine.begin() as connection:
        assert (
            connection.execute(
                text("UPDATE tags SET model_version = 'still-accepted' WHERE method = 'model'")
            ).rowcount
            == 1
        )


def test_downgrade_preserves_proposed_rule_tags(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-proposed-downgrade")
    doc = envelope(scope, "proposed-tag")
    doc["payload"]["items"][0]["model"] = "RTX 3060"
    doc["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060"
    ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    with pg_engine.begin() as connection:
        assert (
            connection.execute(
                text("""
            UPDATE tags SET validation_result = 'proposed', reason = 'rule_not_confirmed'
            WHERE key = 'model_key'
        """)
            ).rowcount
            == 1
        )
    with pytest.raises(
        RuntimeError, match=r"1 incompatible tags.*0 jobs with result.*deliberately"
    ):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "0007_tags")
    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM tags WHERE method = 'rule' "
                    "AND validation_result = 'proposed'"
                )
            ).scalar_one()
            == 1
        )
