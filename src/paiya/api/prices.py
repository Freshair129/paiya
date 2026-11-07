"""Price-trend HTTP routes backed by the shared SQL service."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from paiya.api.evidence import Db, Scope
from paiya.prices.trends import PriceTrendFilters, compute_price_trend

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
    return compute_price_trend(
        conn,
        scope,
        filters,
        datetime.now(UTC),
    )
