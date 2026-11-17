"""Synthetic tests for the user-triggered desktop collection flow."""

from pathlib import Path

from paiya_collector.desktop_controller import DesktopController


class FakeOps:
    def __init__(self):
        self.events = []
        self.ready = True
        self.coverage = "complete"
        self.valid = True
        self.replayed = False
        self.already_ingested = False

    def verify_grant(self):
        self.events.append("grant")

    def inspect_page(self):
        self.events.append("inspect")
        return {"ready": self.ready, "reason": "selectors_unverified"}

    def collect(self):
        self.events.append("collect")
        return {
            "coverage": self.coverage,
            "error_code": None,
            "cards_parsed": 1,
            "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bundle_path": str(Path("synthetic") / "bundle"),
            "replayed": self.replayed,
            "already_ingested": self.already_ingested,
        }

    def validate(self, _path):
        self.events.append("validate")
        return self.valid

    def ingest(self, _run_id):
        self.events.append("ingest")
        return {"state": "done"}


def test_one_manual_run_checks_readiness_then_validates_and_ingests():
    ops = FakeOps()
    result = DesktopController(ops).run_once()
    assert ops.events == ["grant", "inspect", "collect", "validate", "grant", "ingest"]
    assert result["state"] == "done"
    assert result["cards_parsed"] == 1


def test_unverified_page_does_not_collect_or_ingest():
    ops = FakeOps()
    ops.ready = False
    result = DesktopController(ops).run_once()
    assert result["state"] == "not_ready"
    assert ops.events == ["grant", "inspect"]


def test_challenge_or_empty_coverage_does_not_ingest():
    ops = FakeOps()
    ops.coverage = "none"
    result = DesktopController(ops).run_once()
    assert result["state"] == "stopped"
    assert ops.events == ["grant", "inspect", "collect"]


def test_invalid_bundle_is_never_ingested():
    ops = FakeOps()
    ops.valid = False
    result = DesktopController(ops).run_once()
    assert result["state"] == "validation_failed"
    assert ops.events == ["grant", "inspect", "collect", "validate"]


def test_previously_imported_round_is_not_imported_again():
    ops = FakeOps()
    ops.already_ingested = True
    result = DesktopController(ops).run_once()
    assert result["state"] == "already_run"
    assert ops.events == ["grant", "inspect", "collect"]


def test_replayed_ready_bundle_can_resume_import_without_a_new_round():
    ops = FakeOps()
    ops.replayed = True
    result = DesktopController(ops).run_once()
    assert result["state"] == "done"
    assert ops.events == ["grant", "inspect", "collect", "validate", "grant", "ingest"]
