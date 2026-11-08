"""Admin health endpoint guarded by the existing v1 read context."""

from typing import Annotated

from fastapi import APIRouter, Depends

from paiya.api.evidence import require_read_context
from paiya.health.service import get_health_report
from paiya.read.context import ReadContext
from paiya.settings import get_database_engine

router = APIRouter(prefix="/v1")
HealthScope = Annotated[ReadContext, Depends(require_read_context)]


@router.get("/admin/health")
def get_admin_health(_scope: HealthScope) -> dict[str, object]:
    """Return system counts to callers with the same server-selected read access."""
    return get_health_report(get_database_engine())
