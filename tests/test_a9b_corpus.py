"""Replay the A9b synthetic corpus and measure draft labels against actual output."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from paiya.collector.feed_script import FeedScriptAdapter
from paiya.collector.run import execute_discovery
from paiya.groups.service import add_group
from paiya.ingest.service import CaptureConflict, ingest_fixture
from paiya.read.service import read_post
from paiya.scheduler.queue import claim_next, schedule_due
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import groups, observations, posts, sellers, source_grants

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures/acceptance/A9b"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replay(engine, media_root):
    """Derive answers solely from fixture inputs and the system, without labels."""
    discovery = {}
    for path in sorted((CORPUS / "discovery").glob("*/feed.json")):
        feed = load(path)
        first = datetime.fromisoformat(feed["runs"][0]["started_at"].replace("Z", "+00:00"))
        with engine.connect() as conn:
            group = add_group(conn, first - timedelta(minutes=1), feed["group_key"], 30)
            gid = UUID(group["id"])
            grant = conn.execute(
                select(source_grants.c.id).where(source_grants.c.group_id == gid)
            ).scalar_one()
        scope = FixtureScope(gid, grant, feed["group_key"])
        adapter = FeedScriptAdapter(path)
        found = set()
        errors = []
        runs = {}
        for run in feed["runs"]:
            now = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
            with engine.connect() as conn:
                schedule_due(conn, now)
                claim = claim_next(conn, now)
            if claim is None or claim.group_id != gid:
                raise RuntimeError(f"Unexpected discovery claim for {path}: {claim}")
            try:
                result = execute_discovery(
                    engine, scope, claim, adapter, run["label"], now, media_root=media_root
                )
                runs[run["label"]] = sorted(set(result.ingested_keys))
                found.update(result.ingested_keys)
            except CaptureConflict as exc:
                errors.append(f"{run['label']}: {type(exc).__name__}: {exc}")
                with engine.connect() as conn:
                    committed = set(
                        conn.execute(
                            select(posts.c.source_post_key)
                            .select_from(
                                posts.join(observations, observations.c.post_id == posts.c.id)
                            )
                            .where(observations.c.run_id == claim.run_id)
                        ).scalars()
                    )
                runs[run["label"]] = sorted(committed)
                found.update(committed)
                break
        discovery[path.relative_to(CORPUS).as_posix()] = {
            "found": sorted(found),
            "runs": runs,
            "errors": errors,
        }
        with engine.begin() as conn:
            conn.execute(update(groups).where(groups.c.id == gid).values(enabled=False))
    status = {}
    for directory in sorted((CORPUS / "status").iterdir()):
        if not directory.is_dir():
            continue
        documents = [load(p) for p in sorted((directory / "rounds").glob("*.json"))]
        scope = seed_fixture_scope(engine, documents[0]["payload"]["group_key"])
        for doc in documents:
            oid = ingest_fixture(engine, scope, doc, {}, media_root)
        with engine.connect() as conn:
            post_id = conn.execute(
                select(observations.c.post_id).where(observations.c.id == oid)
            ).scalar_one()
            detail = read_post(conn, scope, post_id)
            post = conn.execute(
                select(posts.c.source_post_key, sellers.c.external_actor_key)
                .select_from(posts.outerjoin(sellers, posts.c.seller_id == sellers.c.id))
                .where(posts.c.id == post_id)
            ).one()
            status[directory.relative_to(CORPUS).as_posix()] = {
                "post_key": post.source_post_key,
                "seller_key": post.external_actor_key,
                "items": {
                    item["source_item_key"]: item["sale_status"] for item in detail["current_items"]
                },
            }
    return {"discovery": discovery, "status": status}


def measure(labels, answers):
    rows = labels["cases"]
    discovery = [c for c in rows if c["kind"] == "discovery"]
    status = [c for c in rows if c["kind"] == "status"]
    feeds = {}
    runs = {}
    for feed, result in answers["discovery"].items():
        cases = [c for c in discovery if c["feed"] == feed]
        visible = {c["post_key"] for c in cases if c["labelled_visible"]}
        found = set(result["found"])
        feeds[feed] = (len(found & visible), len(visible))
        for label, run_found in result["runs"].items():
            expected = {
                c["post_key"] for c in cases if c["run_label"] == label and c["labelled_visible"]
            }
            runs[(feed, label)] = (len(set(run_found) & expected), len(expected))

    def actual(c):
        return answers["status"].get(c["scenario"], {})

    def binding(c):
        value = actual(c)
        return (
            value.get("post_key") == c["post_key"]
            and c["item_key"] in value.get("items", {})
            and value.get("seller_key") == c["expected_seller_key"]
        )

    def current(c):
        return actual(c).get("items", {}).get(c["item_key"])

    binding_rows = [c for c in status if c["expected_seller_key"] is not None]
    stable = [c for c in status if c["seller_has_stable_id"]]
    controls = [c for c in status if c["clear_sold_evidence"]]
    return {
        "feeds": feeds,
        "runs": runs,
        "recall": (sum(v[0] for v in feeds.values()), sum(v[1] for v in feeds.values())),
        "unreadable": [c for c in discovery if not c["readable"]],
        "discovery_misses": [
            c
            for c in discovery
            if c["labelled_visible"]
            and c["post_key"] not in answers["discovery"][c["feed"]]["found"]
        ],
        "wrong_binding": [c for c in binding_rows if not binding(c)],
        "binding_total": len(binding_rows),
        "wrong_sold": [
            c for c in status if current(c) == "sold" and c["expected_status"] != "sold"
        ],
        "sold": (sum(current(c) == "sold" for c in controls), len(controls)),
        "sold_misses": [c for c in controls if current(c) != "sold"],
        "stable": (sum(binding(c) for c in stable), len(stable)),
        "no_stable": [c for c in status if not c["seller_has_stable_id"]],
        "status": status,
        "current": current,
    }


def render(labels, answers):
    import hashlib
    from collections import Counter

    m = measure(labels, answers)
    sha = hashlib.sha256((CORPUS / "labels.json").read_bytes()).hexdigest()
    lines = [
        "# ผลวัด A9b — fixture (A)",
        "",
        "ผลนี้ใช้ป้ายร่างที่ยังรอเจ้าของตรวจ",
        "",
        f"labels.json SHA-256: `{sha}` · label_status: `{labels['label_status']}`",
        "ผลนี้เป็น fixture สังเคราะห์ ไม่ใช่ผล pilot",
        "",
        "## การค้นพบ AT-02",
        "",
    ]
    lines.append(f"Corpus total: {len(labels['cases'])}/100 minimum")
    lines.append(f"Visible recall รวม: {m['recall'][0]}/{m['recall'][1]}")
    for feed, (n, d) in m["feeds"].items():
        lines.append(f"- `{feed}`: {n}/{d}")
        for (run_feed, label), (rn, rd) in m["runs"].items():
            if run_feed == feed:
                lines.append(f"  - `{label}`: {rn}/{rd}")
    discovery_count = sum(c["kind"] == "discovery" for c in labels["cases"])
    lines.append(
        f"- Unreadable: {len(m['unreadable'])}/{discovery_count}: "
        + ", ".join(c["case_id"] for c in m["unreadable"])
    )
    lines.append(
        "- Visible misses: " + (", ".join(c["case_id"] for c in m["discovery_misses"]) or "none")
    )
    for feed, result in answers["discovery"].items():
        if result["errors"]:
            lines.append(
                f"- `{feed}`: {'; '.join(result['errors'])}; พบจาก posts ที่ commit ก่อน exception เท่านั้น"  # noqa: E501
            )
    lines += [
        "",
        "## สถานะ AT-03",
        "",
        f"- Wrong binding: {len(m['wrong_binding'])}/{m['binding_total']}: "
        + (", ".join(c["case_id"] for c in m["wrong_binding"]) or "none"),
        f"- Wrong sold: {len(m['wrong_sold'])}/{len(m['status'])}: "
        + (", ".join(c["case_id"] for c in m["wrong_sold"]) or "none"),
        f"- Clear-sold recall: {m['sold'][0]}/{m['sold'][1]}; misses: "
        + (", ".join(c["case_id"] for c in m["sold_misses"]) or "none"),
        f"- Stable-ID seller coverage: {m['stable'][0]}/{m['stable'][1]}",
        f"- No stable ID: {len(m['no_stable'])}/{len(m['status'])}: "
        + ", ".join(c["case_id"] for c in m["no_stable"]),
        "",
        "## ตารางสถานะและคำตอบที่ขาด",
        "",
        "| Expected | open | reserved | sold | unknown | absent | unanswered / total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for expected in ("open", "reserved", "sold", "unknown"):
        cases = [c for c in m["status"] if c["expected_status"] == expected]
        count = Counter(m["current"](c) for c in cases)
        lines.append(
            f"| {expected} | {count['open']} | {count['reserved']} | {count['sold']} | "
            f"{count['unknown']} | {count[None]} | {count['unknown'] + count[None]}/{len(cases)} |"
        )
    unanswered = [c for c in m["status"] if m["current"](c) in (None, "unknown")]
    lines += [
        "",
        "Unanswered cases: " + (", ".join(c["case_id"] for c in unanswered) or "none"),
        "",
        "## ผลกับดัก",
        "",
    ]
    for trap in sorted({c["trap"] for c in m["status"] if c["trap"]}):
        cases = [c for c in m["status"] if c["trap"] == trap]
        ok = [
            c
            for c in cases
            if m["current"](c) == c["expected_status"]
            and (c["expected_seller_key"] is None or c not in m["wrong_binding"])
        ]
        lines.append(
            f"- `{trap}`: {len(ok)}/{len(cases)}; "
            + ", ".join(f"{c['case_id']}={m['current'](c)}" for c in cases)
        )
    lines += [
        "",
        "## ข้อแตกต่างและข้อจำกัด",
        "",
        "- D05-C06: contract unclear — feed หยุดด้วย CaptureConflict ก่อนถึงการ์ดนี้ จึงยังสรุปไม่ได้ว่าป้ายหรือระบบผิด; "  # noqa: E501
        "[collector contract](../../../docs/architecture/contracts/collector-and-query.md) ระบุ “payload_hash ต่างกันเป็น conflict” ส่วน [discovery contract](../../../docs/architecture/contracts/discovery-queue.md) ระบุ “ส่ง envelope เข้า ingest ของ A2” แต่ไม่ได้ระบุผลรอบเมื่อ conflict ระหว่างการ์ด ต้องให้เจ้าของตัดสิน fixture/พฤติกรรมรอบ",  # noqa: E501
        "- D01-C02: ระบบพบ d01-pin-out แม้ไม่ติด visible; "
        "[discovery contract](../../../docs/architecture/contracts/discovery-queue.md) ระบุ “จำนวนการ์ดที่โหลดเห็นทั้งหมดรวม pinned” และ fixture ไม่มีฟิลด์ scope แยกการ์ดนี้ จัดเป็น label looks wrong against the contract; label visible=false ใช้ตัด denominator ไม่ใช่ข้อห้ามพบโพสต์",  # noqa: E501
        "- D05-C05: the original capture IDs differ; the worker derives the same capture ID "
        "for both cards from run and post key, while their seller and item payloads differ.",
        "- Unknown ตามป้ายเป็น unanswered ตามนิยามรายงาน มิได้แปลว่าเป็นความผิดพลาดของระบบ",
        "",
    ]
    return "\n".join(lines)


from test_a2_ingest import pg_engine as pg_engine  # noqa: E402,F401


def test_a9b_thresholds(pg_engine, tmp_path):
    import os

    labels = load(CORPUS / "labels.json")
    answers = replay(pg_engine, tmp_path)
    m = measure(labels, answers)
    if os.environ.get("PAIYA_WRITE_A9B_RESULTS") == "1":
        (CORPUS / "RESULTS.md").write_text(render(labels, answers), encoding="utf-8")
    assert len(labels["cases"]) >= 100
    assert m["recall"][0] / m["recall"][1] >= 0.95
    assert not m["wrong_binding"]
    assert not m["wrong_sold"]
    assert m["sold"][1] >= 20
    assert m["sold"][0] / m["sold"][1] >= 0.95
    assert all(n / d >= 0.95 for n, d in m["runs"].values() if d)


def test_answers_independent_of_labels(pg_engine, tmp_path, monkeypatch):
    import copy

    import pytest
    from test_a2_ingest import pg_engine as fixture

    original = load(CORPUS / "labels.json")
    scrambled = copy.deepcopy(original)
    for case in scrambled["cases"]:
        if case["kind"] == "discovery":
            case["labelled_visible"] = not case["labelled_visible"]
        else:
            case["expected_status"] = "open" if case["expected_status"] != "open" else "sold"
            case["expected_seller_key"] = "scrambled"
    copy_path = tmp_path / "labels-scrambled.json"
    copy_path.write_text(json.dumps(scrambled), encoding="utf-8")
    baseline = replay(pg_engine, tmp_path / "first")
    with pytest.MonkeyPatch.context() as nested:
        generator = fixture.__wrapped__(nested)
        second = next(generator)
        try:
            assert load(copy_path) != original
            read_text = Path.read_text

            def guarded_read(path, *args, **kwargs):
                if path.name == "labels.json":
                    raise AssertionError("replay must not read labels")
                return read_text(path, *args, **kwargs)

            nested.setattr(Path, "read_text", guarded_read)
            repeated = replay(second, tmp_path / "second")
        finally:
            generator.close()
    assert repeated == baseline
