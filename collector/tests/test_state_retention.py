from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from paiya_collector.state import prune_expired_state


def test_state_retention_expires_observations_and_preserves_checkpoint_cursor(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    old = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    recent = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    path = tmp_path / "owner-group-01.json"
    path.write_text(
        json.dumps(
            {
                "group_key": "owner-group-01",
                "checkpoint": {
                    "run_id": "11111111-2222-4333-8444-555555555555",
                    "top_keys": ["old-checkpoint-key"],
                    "advanced_at": old,
                },
                "published_keys": ["old-key", "recent-key", "unknown-age-key"],
                "published_key_seen_at": {"old-key": old, "recent-key": recent},
                "published_runs": {
                    "22222222-2222-4333-8444-555555555555": {
                        "published_at": old,
                        "input_fingerprint": "old-fingerprint",
                    },
                    "33333333-2222-4333-8444-555555555555": {
                        "published_at": recent,
                        "input_fingerprint": "recent-fingerprint",
                    },
                    "44444444-2222-4333-8444-555555555555": {
                        "report": {"bundle_path": "legacy-without-age"}
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    cleanup_id = str(uuid.uuid4())
    result = prune_expired_state(
        path,
        group_key="owner-group-01",
        retention_days=7,
        now=now,
        run_id=cleanup_id,
    )

    assert result == {
        "expired_published_keys": 2,
        "expired_published_runs": 2,
        "expired_checkpoint_keys": 1,
        "checkpoint_preserved": True,
    }
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["published_keys"] == ["recent-key"]
    assert state["published_key_seen_at"] == {"recent-key": recent}
    assert list(state["published_runs"]) == ["33333333-2222-4333-8444-555555555555"]
    assert state["checkpoint"] == {
        "run_id": "11111111-2222-4333-8444-555555555555",
        "top_keys": [],
        "advanced_at": old,
    }

    second = prune_expired_state(
        path,
        group_key="owner-group-01",
        retention_days=7,
        now=now,
        run_id=str(uuid.uuid4()),
    )
    assert second == {
        "expired_published_keys": 0,
        "expired_published_runs": 0,
        "expired_checkpoint_keys": 0,
        "checkpoint_preserved": True,
    }


def test_state_retention_rejects_junction_in_state_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    linked_directory = tmp_path / "linked-state"
    path = linked_directory / "owner-group-01.json"

    def is_junction(candidate: Path) -> bool:
        return candidate == linked_directory.absolute()

    monkeypatch.setattr(Path, "is_junction", is_junction)

    with pytest.raises(ValueError, match="symlink or junction"):
        prune_expired_state(
            path,
            group_key="owner-group-01",
            retention_days=7,
            now=now,
            run_id=str(uuid.uuid4()),
        )
    assert not linked_directory.exists()
