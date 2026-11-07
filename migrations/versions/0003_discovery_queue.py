"""Add the A3 discovery queue, run summaries, and capture gaps.

Revision ID: 0003_discovery_queue
Revises: 0002_fixture_ingest
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_discovery_queue"
down_revision: str | None = "0002_fixture_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("target_key", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("request_digest", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("state", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    op.add_column("capture_runs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("capture_runs", sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.add_column("capture_runs", sa.Column("coverage", sa.Text()))
    op.add_column(
        "capture_runs",
        sa.Column("partial_reasons", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column("capture_runs", sa.Column("stop_reason", sa.Text()))
    op.add_column("capture_runs", sa.Column("cards_seen", sa.Integer()))
    op.add_column("capture_runs", sa.Column("cards_parsed", sa.Integer()))
    op.add_column("capture_runs", sa.Column("scroll_steps", sa.Integer()))
    op.add_column("capture_runs", sa.Column("error_code", sa.Text()))

    bind = op.get_bind()
    capture_run_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("capture_runs")
    }
    if "job_kind" in capture_run_columns:
        # 0002 stores the authoritative value on jobs.kind. Remove only a legacy
        # duplicate column if a database created from an older A2 snapshot has it.
        mismatches = bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM capture_runs AS r
                JOIN jobs AS j ON j.id = r.job_id
                WHERE r.job_kind IS DISTINCT FROM j.kind
                """
            )
        ).scalar_one()
        if mismatches:
            raise RuntimeError("capture_runs.job_kind differs from jobs.kind; refusing to drop it")
        op.drop_column("capture_runs", "job_kind")

    op.execute(
        sa.text(
            """
            UPDATE jobs AS j
            SET target_key = j.logical_key,
                due_at = first_observation.observed_at,
                state = 'succeeded'
            FROM (
                SELECT r.job_id, min(o.observed_at) AS observed_at
                FROM capture_runs AS r
                JOIN observations AS o ON o.run_id = r.id
                GROUP BY r.job_id
            ) AS first_observation
            WHERE first_observation.job_id = j.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE capture_runs AS r
            SET started_at = j.due_at,
                partial_reasons = '{}'::text[]
            FROM jobs AS j
            WHERE j.id = r.job_id
            """
        )
    )

    op.alter_column("jobs", "target_key", existing_type=sa.Text(), nullable=False)
    op.alter_column("jobs", "due_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("jobs", "state", existing_type=sa.Text(), nullable=False)
    op.alter_column(
        "capture_runs", "started_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "capture_runs",
        "partial_reasons",
        existing_type=postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    )

    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('discover', 'revisit', 'manual_import', 'enrich', 'purge')",
    )
    op.create_check_constraint(
        "ck_jobs_state",
        "jobs",
        "state IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_runs_coverage",
        "capture_runs",
        "coverage IS NULL OR coverage IN ('complete', 'partial', 'none')",
    )
    op.create_check_constraint(
        "ck_runs_stop_reason",
        "capture_runs",
        "stop_reason IS NULL OR stop_reason IN ('overlap_reached', 'feed_end', "
        "'time_budget_exhausted', 'scroll_budget_exhausted', 'access_blocked')",
    )

    op.create_table(
        "capture_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gap_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gap_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compensated_by_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name="fk_capture_gaps_group_id_groups"
        ),
        sa.ForeignKeyConstraint(
            ["compensated_by_job_id"], ["jobs.id"], name="fk_capture_gaps_compensated_job"
        ),
        sa.CheckConstraint("gap_to > gap_from", name="ck_capture_gaps_positive_range"),
        sa.CheckConstraint(
            "reason IN ('missed_slots', 'run_failed')", name="ck_capture_gaps_reason"
        ),
    )
    op.create_index("ix_capture_gaps_group_gap_from", "capture_gaps", ["group_id", "gap_from"])
    op.create_index(
        "ix_jobs_queued_due",
        "jobs",
        ["due_at", "id"],
        postgresql_where=sa.text("state = 'queued'"),
    )
    op.create_index(
        "ix_jobs_leased_until",
        "jobs",
        ["lease_until"],
        postgresql_where=sa.text("state = 'leased'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_leased_until", table_name="jobs")
    op.drop_index("ix_jobs_queued_due", table_name="jobs")
    op.drop_index("ix_capture_gaps_group_gap_from", table_name="capture_gaps")
    op.drop_table("capture_gaps")

    op.drop_constraint("ck_runs_stop_reason", "capture_runs", type_="check")
    op.drop_constraint("ck_runs_coverage", "capture_runs", type_="check")
    op.drop_constraint("ck_jobs_state", "jobs", type_="check")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")

    for column in (
        "error_code",
        "scroll_steps",
        "cards_parsed",
        "cards_seen",
        "stop_reason",
        "partial_reasons",
        "coverage",
        "ended_at",
        "started_at",
    ):
        op.drop_column("capture_runs", column)
    for column in (
        "retry_count",
        "lease_until",
        "lease_token",
        "state",
        "due_at",
        "request_digest",
        "target_key",
    ):
        op.drop_column("jobs", column)
