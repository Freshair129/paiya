"""Grant lifecycle operations."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, func, select, update

from paiya.grants.errors import GrantError
from paiya.store.tables import groups, jobs, source_grants


def revoke_grant(engine: Engine, grant_id: UUID) -> dict[str, Any]:
    """Revoke a synthetic grant and cancel its queued or leased jobs atomically."""
    with engine.begin() as conn:
        grant = (
            conn.execute(
                select(source_grants.c.revoked_at, groups.c.group_key)
                .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
                .where(source_grants.c.id == grant_id)
                .with_for_update(of=source_grants)
            )
            .mappings()
            .one_or_none()
        )
        if grant is None:
            raise GrantError("scope_not_found", "The grant was not found")
        if not cast(str, grant["group_key"]).startswith("synthetic-"):
            raise GrantError("scope_not_permitted", "Only synthetic-* grants may be revoked")

        already_revoked = grant["revoked_at"] is not None
        if already_revoked:
            revoked_at = cast(datetime, grant["revoked_at"])
        else:
            revoked_at = cast(
                datetime,
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == grant_id, source_grants.c.revoked_at.is_(None))
                    .values(revoked_at=func.now())
                    .returning(source_grants.c.revoked_at)
                ).scalar_one(),
            )

        cancelled_jobs = len(
            conn.execute(
                update(jobs)
                .where(
                    jobs.c.grant_id == grant_id,
                    jobs.c.state.in_(("queued", "leased")),
                )
                .values(state="cancelled", lease_token=None, lease_until=None)
                .returning(jobs.c.id)
            )
            .scalars()
            .all()
        )

    return {
        "grant_id": str(grant_id),
        "revoked_at": _iso_utc(revoked_at),
        "already_revoked": already_revoked,
        "cancelled_jobs": cancelled_jobs,
    }


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
