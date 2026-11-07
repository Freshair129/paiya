"""Grant-scoped SQL computation for confirmed ask-price trends."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from paiya.read.context import ReadContext
from paiya.read.errors import GrantUnavailable
from paiya.settings import PriceTrendSettings, get_price_trend_settings
from paiya.store.fixture_scope import verify_fixture_scope

_BANGKOK = ZoneInfo("Asia/Bangkok")


@dataclass(frozen=True)
class PriceTrendFilters:
    """Exact comparator values for one price-trend request."""

    model_key: str
    variant: str | None = None
    vram_gb: int | None = None
    condition: str | None = None
    unit: str = "each"
    currency: str = "THB"


_TREND_SQL = text(
    """
    WITH scope_items AS (
        SELECT item.id AS item_id,
               item.model_key,
               item.variant,
               item.vram_gb,
               item.condition,
               post.seller_id
        FROM listing_items AS item
        JOIN posts AS post ON post.id = item.post_id
        WHERE post.group_id = :group_id
          AND item.resolution = 'keyed'
    ),
    scoped_facts AS (
        SELECT fact.item_id,
               fact.observation_id,
               fact.price_kind,
               fact.unit,
               fact.amount,
               fact.amount_max,
               fact.currency,
               fact.validation_result,
               observation.observed_at
        FROM price_facts AS fact
        JOIN observations AS observation
          ON observation.id = fact.observation_id
         AND observation.post_id = fact.post_id
        JOIN scope_items AS item ON item.item_id = fact.item_id
        WHERE observation.group_id = :group_id
          AND observation.grant_id = :grant_id
    ),
    item_activity AS (
        SELECT item_id,
               bool_or(
                   observed_at >= CAST(:previous_from AS timestamptz)
                   AND observed_at < CAST(:window_to AS timestamptz)
               ) AS has_price_in_window,
               bool_or(
                   price_kind = 'ask'
                   AND observed_at >= CAST(:previous_from AS timestamptz)
                   AND observed_at < CAST(:window_to AS timestamptz)
               ) AS has_ask_in_window,
               bool_or(
                   price_kind = 'ask' AND validation_result = 'confirmed'
               ) AS has_confirmed_ask
        FROM scoped_facts
        GROUP BY item_id
    ),
    first_confirmed_ask AS (
        SELECT DISTINCT ON (item_id)
               item_id,
               observed_at,
               observation_id,
               unit,
               amount,
               amount_max,
               currency
        FROM scoped_facts
        WHERE price_kind = 'ask'
          AND validation_result = 'confirmed'
        ORDER BY item_id, observed_at, observation_id
    ),
    item_results AS (
        SELECT item.item_id,
               item.seller_id,
               first_ask.observed_at AS sample_at,
               first_ask.amount AS sample_amount,
               first_ask.amount_max AS sample_amount_max,
               CASE
                   WHEN item.model_key IS NULL THEN 'unclassified'
                   WHEN item.model_key IS DISTINCT FROM CAST(:model_key AS text)
                     OR item.variant IS DISTINCT FROM CAST(:variant AS text)
                     OR CAST(item.vram_gb AS integer)
                        IS DISTINCT FROM CAST(:vram_gb AS integer)
                     OR item.condition IS DISTINCT FROM CAST(:condition AS text)
                       THEN NULL
                   WHEN NOT activity.has_ask_in_window THEN 'non_ask'
                   WHEN NOT activity.has_confirmed_ask THEN 'unconfirmed'
                   WHEN first_ask.observed_at IS NULL
                     OR first_ask.observed_at < CAST(:previous_from AS timestamptz)
                     OR first_ask.observed_at >= CAST(:window_to AS timestamptz)
                     OR first_ask.currency IS DISTINCT FROM CAST(:currency AS text)
                       THEN NULL
                   WHEN first_ask.unit IN ('bundle', 'unknown')
                       THEN 'bundle_or_unknown_unit'
                   WHEN first_ask.amount_max IS NOT NULL THEN 'range'
                   ELSE 'sample'
               END AS outcome
        FROM scope_items AS item
        JOIN item_activity AS activity ON activity.item_id = item.item_id
        LEFT JOIN first_confirmed_ask AS first_ask ON first_ask.item_id = item.item_id
        WHERE activity.has_price_in_window
    ),
    classified AS (
        SELECT item_id,
               seller_id,
               sample_amount,
               outcome,
               CASE
                   WHEN outcome = 'sample'
                    AND sample_at >= CAST(:current_from AS timestamptz)
                    AND sample_at < CAST(:window_to AS timestamptz) THEN 'current'
                   WHEN outcome = 'sample'
                    AND sample_at >= CAST(:previous_from AS timestamptz)
                    AND sample_at < CAST(:current_from AS timestamptz) THEN 'previous'
                   ELSE NULL
               END AS period
        FROM item_results
    ),
    samples AS (
        SELECT seller_id, sample_amount AS amount, period
        FROM classified
        WHERE outcome = 'sample'
    ),
    statistics AS (
        SELECT COUNT(*) FILTER (WHERE period = 'current')::integer AS current_n,
               COUNT(DISTINCT seller_id) FILTER (WHERE period = 'current')::integer
                   AS current_sellers,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'current') AS current_median_raw,
               percentile_cont(0.25) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'current') AS current_p25_raw,
               percentile_cont(0.75) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'current') AS current_p75_raw,
               COUNT(*) FILTER (WHERE period = 'previous')::integer AS previous_n,
               COUNT(DISTINCT seller_id) FILTER (WHERE period = 'previous')::integer
                   AS previous_sellers,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'previous') AS previous_median_raw,
               percentile_cont(0.25) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'previous') AS previous_p25_raw,
               percentile_cont(0.75) WITHIN GROUP (
                   ORDER BY amount::double precision
               ) FILTER (WHERE period = 'previous') AS previous_p75_raw
        FROM samples
    ),
    excluded AS (
        SELECT COUNT(*) FILTER (WHERE outcome = 'unclassified')::integer AS unclassified,
               COUNT(*) FILTER (WHERE outcome = 'non_ask')::integer AS non_ask,
               COUNT(*) FILTER (
                   WHERE outcome = 'bundle_or_unknown_unit'
               )::integer AS bundle_or_unknown_unit,
               COUNT(*) FILTER (WHERE outcome = 'range')::integer AS excluded_range,
               COUNT(*) FILTER (WHERE outcome = 'unconfirmed')::integer AS unconfirmed
        FROM item_results
    ),
    windows AS (
        SELECT 'previous'::text AS period,
               CAST(:previous_from AS timestamptz) AS from_at,
               CAST(:current_from AS timestamptz) AS to_at
        UNION ALL
        SELECT 'current'::text,
               CAST(:current_from AS timestamptz),
               CAST(:window_to AS timestamptz)
    ),
    coverage_groups AS (
        SELECT id AS group_id, poll_minutes
        FROM groups
        WHERE id = :group_id
          AND poll_minutes IS NOT NULL
          AND poll_minutes > 0
    ),
    scheduled_slots AS (
        SELECT coverage_group.group_id,
               period_window.period,
               COUNT(slot.slot_at)::integer AS scheduled_count
        FROM coverage_groups AS coverage_group
        CROSS JOIN windows AS period_window
        LEFT JOIN LATERAL generate_series(
            to_timestamp(
                (
                    ceil(
                        extract(epoch FROM period_window.from_at)::numeric
                        / (coverage_group.poll_minutes * 60)
                    ) * (coverage_group.poll_minutes * 60)
                )::double precision
            ),
            period_window.to_at - interval '1 microsecond',
            make_interval(mins => coverage_group.poll_minutes)
        ) AS slot(slot_at) ON true
        GROUP BY coverage_group.group_id, period_window.period
    ),
    successful_slots AS (
        SELECT DISTINCT job.group_id,
               period_window.period,
               parsed.slot_at
        FROM jobs AS job
        JOIN coverage_groups AS coverage_group ON coverage_group.group_id = job.group_id
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN job.logical_key ~
                     '^discover:[^:]+:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
                 AND split_part(job.logical_key, ':', 2) = job.group_id::text
                    THEN substring(job.logical_key FROM '^discover:[^:]+:(.+)$')::timestamptz
                ELSE NULL
            END AS slot_at
        ) AS parsed
        JOIN windows AS period_window
          ON parsed.slot_at >= period_window.from_at
         AND parsed.slot_at < period_window.to_at
        WHERE job.kind = 'discover'
          AND job.state = 'succeeded'
    ),
    coverage_counts AS (
        SELECT scheduled.group_id,
               scheduled.period,
               scheduled.scheduled_count,
               COUNT(DISTINCT success.slot_at)::integer AS successful_count
        FROM scheduled_slots AS scheduled
        LEFT JOIN successful_slots AS success
          ON success.group_id = scheduled.group_id
         AND success.period = scheduled.period
        GROUP BY scheduled.group_id, scheduled.period, scheduled.scheduled_count
    ),
    coverage_groups_with_success AS (
        SELECT DISTINCT group_id, period
        FROM successful_slots
    ),
    coverage AS (
        SELECT COALESCE(
                   (
                       SELECT bool_or(successful_count * 2 < scheduled_count)
                       FROM coverage_counts
                   ),
                   false
               )
               OR EXISTS (
                   SELECT 1
                   FROM coverage_groups_with_success AS previous_group
                   WHERE previous_group.period = 'previous'
                     AND NOT EXISTS (
                         SELECT 1
                         FROM coverage_groups_with_success AS current_group
                         WHERE current_group.period = 'current'
                           AND current_group.group_id = previous_group.group_id
                     )
               )
               OR EXISTS (
                   SELECT 1
                   FROM coverage_groups_with_success AS current_group
                   WHERE current_group.period = 'current'
                     AND NOT EXISTS (
                         SELECT 1
                         FROM coverage_groups_with_success AS previous_group
                         WHERE previous_group.period = 'previous'
                           AND previous_group.group_id = current_group.group_id
                     )
               ) AS coverage_differs
    ),
    statuses AS (
        SELECT statistics.*,
               CASE
                   WHEN current_n >= :min_offers AND current_sellers >= :min_sellers
                       THEN 'ok'
                   ELSE 'insufficient_data'
               END AS current_status,
               CASE
                   WHEN previous_n >= :min_offers AND previous_sellers >= :min_sellers
                       THEN 'ok'
                   ELSE 'insufficient_data'
               END AS previous_status
        FROM statistics
    ),
    metrics AS (
        SELECT statuses.*,
               excluded.unclassified,
               excluded.non_ask,
               excluded.bundle_or_unknown_unit,
               excluded.excluded_range,
               excluded.unconfirmed,
               coverage.coverage_differs,
               CASE
                   WHEN current_status <> 'ok'
                     OR previous_status <> 'ok'
                     OR previous_median_raw = 0
                       THEN 'insufficient_data'
                   WHEN coverage.coverage_differs THEN 'coverage_differs'
                   ELSE NULL
               END AS direction_reason,
               CASE
                   WHEN current_status = 'ok'
                    AND previous_status = 'ok'
                    AND previous_median_raw > 0
                    AND NOT coverage.coverage_differs
                       THEN round(
                           (
                               CAST(current_median_raw AS numeric)
                               / CAST(previous_median_raw AS numeric)
                               - 1
                           ) * 100,
                           2
                       )
                   ELSE NULL
               END AS change_pct_raw
        FROM statuses
        CROSS JOIN excluded
        CROSS JOIN coverage
    )
    SELECT current_status,
           current_n,
           current_sellers,
           CASE WHEN current_status = 'ok'
                THEN CAST(round(CAST(current_median_raw AS numeric), 2) AS text)
                ELSE NULL END AS current_median,
           CASE WHEN current_status = 'ok'
                THEN CAST(round(CAST(current_p25_raw AS numeric), 2) AS text)
                ELSE NULL END AS current_p25,
           CASE WHEN current_status = 'ok'
                THEN CAST(round(CAST(current_p75_raw AS numeric), 2) AS text)
                ELSE NULL END AS current_p75,
           previous_status,
           previous_n,
           previous_sellers,
           CASE WHEN previous_status = 'ok'
                THEN CAST(round(CAST(previous_median_raw AS numeric), 2) AS text)
                ELSE NULL END AS previous_median,
           CASE WHEN previous_status = 'ok'
                THEN CAST(round(CAST(previous_p25_raw AS numeric), 2) AS text)
                ELSE NULL END AS previous_p25,
           CASE WHEN previous_status = 'ok'
                THEN CAST(round(CAST(previous_p75_raw AS numeric), 2) AS text)
                ELSE NULL END AS previous_p75,
           CASE
               WHEN direction_reason IS NULL AND change_pct_raw >= :flat_band_pct THEN 'up'
               WHEN direction_reason IS NULL AND change_pct_raw <= -:flat_band_pct THEN 'down'
               WHEN direction_reason IS NULL THEN 'flat'
               ELSE NULL
           END AS direction,
           direction_reason,
           CASE WHEN direction_reason IS NULL
                THEN CAST(change_pct_raw AS text)
                ELSE NULL END AS change_pct,
           unclassified,
           non_ask,
           bundle_or_unknown_unit,
           excluded_range,
           unconfirmed
    FROM metrics
    """
)


def _filters_from_value(
    filters: PriceTrendFilters | Mapping[str, object],
) -> PriceTrendFilters:
    if isinstance(filters, PriceTrendFilters):
        result = filters
    else:
        model_key = filters.get("model_key")
        variant = filters.get("variant")
        vram_gb = filters.get("vram_gb")
        condition = filters.get("condition")
        unit = filters["unit"] if "unit" in filters else "each"
        currency = filters["currency"] if "currency" in filters else "THB"
        if not isinstance(model_key, str) or not model_key:
            raise ValueError("model_key is required")
        if variant is not None and not isinstance(variant, str):
            raise ValueError("variant must be a string or null")
        if vram_gb is not None and (not isinstance(vram_gb, int) or isinstance(vram_gb, bool)):
            raise ValueError("vram_gb must be an integer or null")
        if condition is not None and not isinstance(condition, str):
            raise ValueError("condition must be a string or null")
        if not isinstance(unit, str) or unit != "each":
            raise ValueError("unit must be 'each'")
        if not isinstance(currency, str) or not currency:
            raise ValueError("currency must be a non-empty string")
        result = PriceTrendFilters(
            model_key=model_key,
            variant=variant,
            vram_gb=vram_gb,
            condition=condition,
            unit=unit,
            currency=currency,
        )

    if not result.model_key:
        raise ValueError("model_key is required")
    if result.unit != "each":
        raise ValueError("unit must be 'each'")
    if not result.currency:
        raise ValueError("currency must be a non-empty string")
    if result.vram_gb is not None and (
        not isinstance(result.vram_gb, int) or isinstance(result.vram_gb, bool)
    ):
        raise ValueError("vram_gb must be an integer or null")
    return result


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _period_result(row: Mapping[str, object], name: str) -> dict[str, object]:
    return {
        "status": row[f"{name}_status"],
        "n": int(cast(int, row[f"{name}_n"])),
        "sellers": int(cast(int, row[f"{name}_sellers"])),
        "median": row[f"{name}_median"],
        "p25": row[f"{name}_p25"],
        "p75": row[f"{name}_p75"],
    }


def compute_price_trend(
    conn: Connection,
    context: ReadContext,
    filters: PriceTrendFilters | Mapping[str, object],
    now: datetime,
) -> dict[str, object]:
    """Compute two closed Bangkok-day windows within one trusted grant."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    selected_filters = _filters_from_value(filters)
    settings: PriceTrendSettings = get_price_trend_settings()
    try:
        verify_fixture_scope(conn, context, "read")
    except PermissionError as error:
        raise GrantUnavailable() from error

    today: date = now.astimezone(_BANGKOK).date()
    window_to_local = datetime.combine(today, time.min, tzinfo=_BANGKOK)
    current_from_local = window_to_local - timedelta(days=settings.window_days)
    previous_from_local = current_from_local - timedelta(days=settings.window_days)
    window_to = window_to_local.astimezone(UTC)
    current_from = current_from_local.astimezone(UTC)
    previous_from = previous_from_local.astimezone(UTC)

    row = cast(
        Mapping[str, object],
        conn.execute(
            _TREND_SQL,
            {
                "group_id": context.group_id,
                "grant_id": context.grant_id,
                "model_key": selected_filters.model_key,
                "variant": selected_filters.variant,
                "vram_gb": selected_filters.vram_gb,
                "condition": selected_filters.condition,
                "currency": selected_filters.currency,
                "previous_from": previous_from,
                "current_from": current_from,
                "window_to": window_to,
                "min_offers": settings.min_offers,
                "min_sellers": settings.min_sellers,
                "flat_band_pct": settings.flat_band_pct,
            },
        )
        .mappings()
        .one(),
    )

    return {
        "label": "ราคาตั้งขาย",
        "filters": {
            "model_key": selected_filters.model_key,
            "variant": selected_filters.variant,
            "vram_gb": selected_filters.vram_gb,
            "condition": selected_filters.condition,
            "unit": selected_filters.unit,
            "currency": selected_filters.currency,
        },
        "window": {
            "timezone": "Asia/Bangkok",
            "current": {"from": _iso_utc(current_from), "to": _iso_utc(window_to)},
            "previous": {"from": _iso_utc(previous_from), "to": _iso_utc(current_from)},
        },
        "current": _period_result(row, "current"),
        "previous": _period_result(row, "previous"),
        "direction": row["direction"],
        "direction_reason": row["direction_reason"],
        "change_pct": row["change_pct"],
        "excluded": {
            "unclassified": int(cast(int, row["unclassified"])),
            "non_ask": int(cast(int, row["non_ask"])),
            "bundle_or_unknown_unit": int(cast(int, row["bundle_or_unknown_unit"])),
            "range": int(cast(int, row["excluded_range"])),
            "unconfirmed": int(cast(int, row["unconfirmed"])),
        },
        "limitations": ["one_offer_per_item"],
        "rule_version": "trend-v1.0",
        "settings": {
            "window_days": settings.window_days,
            "min_offers": settings.min_offers,
            "min_sellers": settings.min_sellers,
            "flat_band_pct": settings.flat_band_pct,
        },
    }
