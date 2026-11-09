"""Price-trend HTTP routes backed by the shared SQL service."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from paiya.api.evidence import Db, Scope
from paiya.prices.trends import PriceTrendFilters, compute_price_trend
from paiya.read.context import ReadableGrant, ReadContext
from paiya.read.errors import GrantUnavailable
from paiya.read.service import active_readable_grants, verify_read_access
from paiya.store.fixture_scope import FixtureScope

router = APIRouter(prefix="/v1")


@router.get("/prices/trends")
def get_price_trend(
    scope: Scope,
    conn: Db,
    model_key: Annotated[str, Query(min_length=1)],
    variant: str | None = None,
    vram_gb: int | None = None,
    condition: str | None = None,
    unit: Literal["each"] = "each",
    currency: str = "THB",
) -> dict[str, object]:
    """Return the trend for one exact model/variant/condition comparator."""
    filters = PriceTrendFilters(
        model_key=model_key,
        variant=variant,
        vram_gb=vram_gb,
        condition=condition,
        unit=unit,
        currency=currency,
    )
    selected_scope: ReadContext
    if isinstance(scope, list):
        grants = active_readable_grants(conn, scope)
        if len(grants) != 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "read_scope_ambiguous",
                    "message": "Price trends require one active group grant",
                },
            )
        grant = grants[0]
        selected_scope = _fixture_context_for_trend(grant)
    elif isinstance(scope, ReadableGrant):
        verify_read_access(conn, scope)
        selected_scope = _fixture_context_for_trend(scope)
    else:
        selected_scope = scope
    return compute_price_trend(
        conn,
        selected_scope,
        filters,
        datetime.now(UTC),
    )


def _fixture_context_for_trend(grant: ReadableGrant) -> ReadContext:
    """Adapt the A7a single-group fixture service after rechecking the owner grant."""
    if grant.group_source != "fixture" or grant.source_kind != "fixture":
        raise GrantUnavailable()
    return FixtureScope(
        group_id=grant.group_id,
        grant_id=grant.grant_id,
        group_key=grant.group_key,
    )
