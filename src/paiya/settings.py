"""Environment-only application configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from sqlalchemy import Engine, create_engine


@dataclass(frozen=True)
class PriceTrendSettings:
    """Runtime thresholds returned with every trend result."""

    window_days: int = 7
    min_offers: int = 10
    min_sellers: int = 3
    flat_band_pct: int = 5


@dataclass(frozen=True)
class EnrichSettings:
    """Runtime configuration for the loopback-only enrichment worker."""

    enabled: bool
    ollama_url: str
    ollama_model: str
    timeout_seconds: int


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


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be 'true' or 'false'; got {raw_value!r}")


def get_enrich_enabled() -> bool:
    """Read only the enable switch for use by the ingest transaction."""
    return _boolean("PAIYA_ENRICH_ENABLED", False)


def get_disk_min_free_bytes() -> int:
    """Return the configured minimum free space for media storage."""
    return _positive_int("PAIYA_DISK_MIN_FREE_BYTES", 5 * 1024**3)


def get_job_overdue_minutes() -> int:
    """Return the configured queued-job overdue threshold."""
    return _positive_int("PAIYA_JOB_OVERDUE_MINUTES", 15)


def get_enrich_settings() -> EnrichSettings:
    """Read and validate enrichment settings before a worker or server starts."""
    enabled = get_enrich_enabled()
    raw_url = os.environ.get("PAIYA_OLLAMA_URL", "http://127.0.0.1:11434").strip()
    try:
        parsed_url = urlsplit(raw_url)
        hostname = parsed_url.hostname
        port = parsed_url.port
    except ValueError as error:
        raise ValueError("PAIYA_OLLAMA_URL must be a valid loopback HTTP URL") from error

    if (
        parsed_url.scheme not in {"http", "https"}
        or hostname is None
        or hostname.casefold() not in {"127.0.0.1", "localhost", "::1"}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            "PAIYA_OLLAMA_URL must use http(s) and a loopback host: 127.0.0.1, localhost, or [::1]"
        )

    model = os.environ.get("PAIYA_OLLAMA_MODEL", "qwen3:4b").strip()
    if not model:
        raise ValueError("PAIYA_OLLAMA_MODEL must be a non-empty model name")

    return EnrichSettings(
        enabled=enabled,
        ollama_url=raw_url.rstrip("/"),
        ollama_model=model,
        timeout_seconds=_positive_int("PAIYA_ENRICH_TIMEOUT_SECONDS", 60),
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
