"""Alembic environment. Database credentials come only from the process environment."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, create_engine, pool

from paiya.settings import get_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A0 contains no business tables. Later migrations will register their metadata.
target_metadata = MetaData()


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
