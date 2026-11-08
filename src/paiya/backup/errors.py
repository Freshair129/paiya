"""Typed failures for O1a backup and restore operations."""

from collections.abc import Mapping


class BackupError(ValueError):
    """A fail-closed backup operation error with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
