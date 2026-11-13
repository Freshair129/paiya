"""Allow replayable Collector run expiry."""

from alembic import op

revision = "0012_collector_retention"
down_revision = "0011_owner_sessions"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_purge_jobs_scope_kind"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "purge_jobs", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "purge_jobs", "scope_kind IN ('grant', 'post', 'collector_run')"
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "purge_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "purge_jobs", "scope_kind IN ('grant', 'post')")
