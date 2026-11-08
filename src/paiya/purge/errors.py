"""Errors raised by the synthetic-scope purge command."""


class PurgeError(ValueError):
    """A purge request was refused before data changes or failed atomically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
