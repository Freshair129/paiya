"""Environment-only application configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine


@dataclass(frozen=True)
class PriceTrendSettings:
    """Runtime thresholds returned with every trend result."""

    window_days: int = 7
    min_offers: int = 10
    min_sellers: int = 3
    flat_band_pct: int = 5


def _positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer; got {raw_value!r}") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer; got {raw_value!r}")
    return value


def get_price_trend_settings() -> PriceTrendSettings:
    """Read and validate environment-configured trend thresholds."""
    return PriceTrendSettings(
        window_days=_positive_int("PAIYA_TREND_WINDOW_DAYS", 7),
        min_offers=_positive_int("PAIYA_TREND_MIN_OFFERS", 10),
        min_sellers=_positive_int("PAIYA_TREND_MIN_SELLERS", 3),
        flat_band_pct=_positive_int("PAIYA_TREND_FLAT_BAND_PCT", 5),
    )


def get_database_url() -> str:
    """Read the development or deployment database URL when it is needed."""
    url = os.environ.get("PAIYA_DATABASE_URL")
    if not url:
        raise RuntimeError("PAIYA_DATABASE_URL is required for database operations")
    return url


@lru_cache(maxsize=1)
def get_database_engine() -> Engine:
    """Create and retain the process-wide database engine."""
    return create_engine(get_database_url(), pool_pre_ping=True)
