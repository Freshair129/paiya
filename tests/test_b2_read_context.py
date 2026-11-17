from uuid import uuid4

import pytest

from paiya.api import evidence
from paiya.read.context import ReadableGrant
from paiya.store.fixture_scope import FixtureScope


@pytest.mark.parametrize("mode", ["fixture", "owner", "empty_owner"])
def test_groups_reports_read_context_without_exposing_grant_details(monkeypatch, mode):
    group_id, grant_id = uuid4(), uuid4()
    fixture = FixtureScope(group_id=group_id, grant_id=grant_id, group_key="synthetic-test")
    grant = ReadableGrant(
        group_id=group_id,
        grant_id=grant_id,
        group_key="owner-group-01",
        group_source="facebook",
        source_kind="facebook_live",
    )
    scope = fixture if mode == "fixture" else ([grant] if mode == "owner" else [])
    monkeypatch.setattr(evidence, "read_groups", lambda _conn, _scope: {"groups": []})
    result = evidence.get_groups(scope=scope, conn=object())
    assert result == {"groups": [], "read_context": "fixture" if mode == "fixture" else "owner"}
