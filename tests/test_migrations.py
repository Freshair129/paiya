"""Real PostgreSQL 17 migration smoke test. Never silently skip this test."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_and_downgrade_on_postgres17(monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert revision == "0002_fixture_ingest"
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
                == "0002_fixture_ingest"
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()
