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
        assert revision == "0006_price_validation"
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
                == "0006_price_validation"
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()
