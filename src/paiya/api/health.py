"""Admin health endpoint guarded by the existing v1 read context."""

from fastapi import APIRouter

from paiya.api.evidence import Db, Scope
from paiya.health.service import get_health_report
from paiya.read.service import verify_read_access
from paiya.settings import get_database_engine

router = APIRouter(prefix="/v1")


@router.get("/admin/health")
def get_admin_health(scope: Scope, conn: Db) -> dict[str, object]:
    """Return system counts to callers with the same server-selected read access."""
    verify_read_access(conn, scope)
    return get_health_report(get_database_engine())
