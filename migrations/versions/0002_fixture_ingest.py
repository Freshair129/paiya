"""Fixture-only ingestion records, provenance and scoped read model.

Revision ID: 0002_fixture_ingest
Revises: 0001_initial_empty
"""

from collections.abc import Sequence

from alembic import op
from paiya.store.tables import metadata

revision: str = "0002_fixture_ingest"
down_revision: str | None = "0001_initial_empty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Metadata is pinned to this A2 schema until the next migration.
    for table in metadata.sorted_tables:
        table.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    for table in reversed(metadata.sorted_tables):
        table.drop(op.get_bind(), checkfirst=False)
