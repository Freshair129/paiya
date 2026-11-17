"""Synthetic Collector-to-owner-API acceptance using PostgreSQL 17."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_i2_inbox_ingest import _call, _grant
from test_i2_inbox_ingest import collector_db as collector_db  # noqa: F401
from test_i2_inbox_ingest import inbox_setup as inbox_setup  # noqa: F401
from test_s1_owner_session import client as client  # noqa: F401

from paiya.store.tables import posts, source_grants

ROOT = Path(__file__).resolve().parents[1]


def test_collector_bundle_reaches_authenticated_owner_posts_api(
    collector_db: Engine,
    inbox_setup: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client,
    authenticated_session,
) -> None:
    """Run the actual Collector with a synthetic browser, then ingest and read its post."""
    inbox, _ = inbox_setup
    grant_id = _grant(capsys)
    with collector_db.begin() as conn:
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == grant_id)
            .values(valid_from=datetime.now(UTC) - timedelta(hours=1))
        )
    # Execute the separate Collector with its own import path, as in deployment.
    script = r"""
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from paiya_collector.live import discover_browser
from test_live_discovery import FakePage, fake_attacher, page_snapshot, post, write_live_config
root, temporary, inbox = map(Path, sys.argv[1:4])
grant = sys.argv[4]
config = write_live_config(temporary / "config.json")
value = json.loads(config.read_text(encoding="utf-8"))
value["grant_id"] = grant
config.write_text(json.dumps(value), encoding="utf-8")
image = (root / "collector/fixtures/images/rtx3060.jpg").read_bytes()
page = FakePage([page_snapshot((post(image),))])
report = discover_browser(config, profile_dir=temporary / "profile", inbox_root=inbox,
    state_dir=temporary / "state", browser_attacher=fake_attacher(page),
    repository_root=temporary / "repo", now=datetime.now(UTC)-timedelta(minutes=1))
print(json.dumps(report))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "collector"), str(ROOT / "collector" / "tests")]
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(ROOT), str(tmp_path), str(inbox), str(grant_id)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    report = json.loads(result.stdout)
    assert report["cards_parsed"] == 1
    assert report["coverage"] == "complete"
    assert _call(capsys, "inbox", "ingest", report["run_id"])["result"]["state"] == "done"
    monkeypatch.setenv("PAIYA_DEV_FIXTURE_MODE", "0")
    response = client.get("/v1/posts", headers={"Host": "127.0.0.1:8137"})
    assert response.status_code == 200, response.text
    with collector_db.connect() as conn:
        post_ids = {str(row) for row in conn.execute(select(posts.c.id)).scalars()}
    assert len(post_ids) == 1
    assert {post["post_id"] for post in response.json()["posts"]} == post_ids
