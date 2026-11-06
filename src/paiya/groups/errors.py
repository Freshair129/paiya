"""Stable domain errors for local group commands."""


class GroupError(ValueError):
    """A group command was rejected without changing its database state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
