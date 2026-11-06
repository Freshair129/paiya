"""Environment-only application configuration."""

import os


def get_database_url() -> str:
    """Read the development or deployment database URL when it is needed."""
    url = os.environ.get("PAIYA_DATABASE_URL")
    if not url:
        raise RuntimeError("PAIYA_DATABASE_URL is required for database operations")
    return url
