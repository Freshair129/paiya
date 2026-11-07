"""Stable domain errors for local watch commands."""


class WatchError(ValueError):
    """A watch command was rejected without changing its database state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
