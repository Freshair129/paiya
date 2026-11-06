"""Domain errors for fixture-scoped read operations."""


class ReadError(Exception):
    """A read request failed with a stable API-facing domain code."""

    status_code = 500
    code = "internal_error"
    default_message = "The read request could not be completed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class ReadContextRequired(ReadError):
    status_code = 401
    code = "read_context_required"
    default_message = "A server-provided read context is required"


class GrantUnavailable(ReadError):
    status_code = 403
    code = "grant_unavailable"
    default_message = "The fixture grant is unavailable for this operation"


class NotFound(ReadError):
    status_code = 404
    code = "not_found"
    default_message = "The requested resource was not found"


class MediaNotAvailable(ReadError):
    status_code = 404
    code = "media_not_available"
    default_message = "No captured media is available for this evidence"


class MediaIntegrityFailure(ReadError):
    status_code = 500
    code = "media_integrity_failure"
    default_message = "Stored media is missing or failed its integrity check"


class MediaStorageUnavailable(ReadError):
    status_code = 503
    code = "media_storage_unavailable"
    default_message = "Fixture media storage is not configured or unavailable"
