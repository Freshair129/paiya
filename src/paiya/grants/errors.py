"""Stable domain errors for grant commands."""


class GrantError(ValueError):
    """A grant command was rejected without changing its database state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
