"""Small transaction helper for services that accept a caller-owned connection."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection


@contextmanager
def atomic(conn: Connection) -> Iterator[None]:
    """Commit a service call, or use a savepoint inside a caller transaction."""
    if conn.in_transaction():
        with conn.begin_nested():
            yield
    else:
        with conn.begin():
            yield
