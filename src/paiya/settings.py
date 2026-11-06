"""Environment-only application configuration."""

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine


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
