"""Explicitly gated process-crash hooks for synthetic continuity exercises."""

import os

FAULT_POINTS = frozenset({"after_media_write", "after_ingest_commit", "before_job_complete"})


class FaultConfigurationError(ValueError):
    """A fault point was configured without the explicit local safety switch."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_fault_configuration() -> None:
    """Reject stale or unsafe fault settings before a worker touches the queue."""
    point = os.environ.get("PAIYA_FAULT_POINT")
    if point is None:
        return
    if os.environ.get("PAIYA_ALLOW_FAULT_INJECTION") != "1":
        raise FaultConfigurationError(
            "fault_injection_not_allowed",
            "PAIYA_FAULT_POINT requires PAIYA_ALLOW_FAULT_INJECTION=1",
        )
    if point not in FAULT_POINTS:
        raise FaultConfigurationError("fault_point_invalid", "PAIYA_FAULT_POINT is not supported")


def maybe_fault(point: str) -> None:
    """Exit immediately at a configured point; otherwise leave execution untouched."""
    if (
        os.environ.get("PAIYA_ALLOW_FAULT_INJECTION") == "1"
        and os.environ.get("PAIYA_FAULT_POINT") == point
    ):
        os._exit(86)
