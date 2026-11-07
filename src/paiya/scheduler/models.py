"""Small immutable values returned by scheduler operations."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class JobClaim:
    job_id: UUID
    group_id: UUID
    grant_id: UUID
    run_id: UUID
    kind: str
    logical_key: str
    target_key: str
    due_at: datetime
    lease_token: UUID
    lease_until: datetime


@dataclass(frozen=True)
class EnrichJobClaim:
    job_id: UUID
    group_id: UUID
    grant_id: UUID
    target_key: str
    lease_token: UUID
    lease_until: datetime
