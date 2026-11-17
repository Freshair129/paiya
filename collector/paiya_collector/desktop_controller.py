"""Single user-triggered Collector round for the local desktop interface."""

from threading import Lock
from typing import Any, Protocol
from uuid import UUID


class DesktopOperations(Protocol):
    def verify_grant(self) -> None: ...

    def inspect_page(self) -> dict[str, Any]: ...

    def collect(self) -> dict[str, Any]: ...

    def pending(self) -> dict[str, Any] | None: ...

    def validate(self, path: str) -> bool: ...

    def ingest(self, run_id: UUID) -> dict[str, Any]: ...


class DesktopController:
    """Keep readiness, publication and import ordered; permit only one active round."""

    def __init__(self, operations: DesktopOperations) -> None:
        self.operations = operations
        self._lock = Lock()

    def run_once(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"state": "busy"}
        try:
            self.operations.verify_grant()
            readiness = self.operations.inspect_page()
            if not readiness.get("ready"):
                return {"state": "not_ready", "reason": readiness.get("reason")}
            report = self.operations.collect()
            return self._import_report(report)
        finally:
            self._lock.release()

    def resume_pending(self) -> dict[str, Any]:
        """Import an existing approved bundle without opening the source."""
        if not self._lock.acquire(blocking=False):
            return {"state": "busy"}
        try:
            self.operations.verify_grant()
            report = self.operations.pending()
            return {"state": "no_pending"} if report is None else self._import_report(report)
        finally:
            self._lock.release()

    def _import_report(self, report: dict[str, Any]) -> dict[str, Any]:
        if report.get("already_ingested"):
            return {"state": "already_run"}
        if report.get("coverage") == "none" or report.get("error_code"):
            return {"state": "stopped", "reason": report.get("error_code")}
        if not self.operations.validate(str(report["bundle_path"])):
            return {"state": "validation_failed"}
        self.operations.verify_grant()
        receipt = self.operations.ingest(UUID(str(report["run_id"])))
        return {
            "state": receipt.get("state", "failed"),
            "cards_parsed": int(report.get("cards_parsed", 0)),
            "coverage": report.get("coverage"),
        }
