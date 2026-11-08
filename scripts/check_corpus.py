"""Validate the A9b synthetic acceptance corpus without evaluating Paiya logic.

Labels remain human-review drafts. This script validates fixture structure,
references, required corpus mix, and the generated Thai review sheet only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from check_contracts import problems as observation_problems
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "fixtures/acceptance/A9b"
FEED_SCHEMA = ROOT / "fixtures/feeds/feed.schema.json"
OBSERVATION_SCHEMA = ROOT / "docs/architecture/contracts/observation.v1.schema.json"
FEED_DIR_RE = re.compile(r"^D\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
STATUS_DIR_RE = re.compile(r"^T\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
ROUND_RE = re.compile(r"^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
EXPECTED_STATUSES = {"open", "reserved", "sold", "unknown"}
TRAPS = {
    "buyer_comment_claimed_seller",
    "seller_role_actor_mismatch",
    "unlinked_closing_evidence",
    "two_items_one_sells",
    "reserved_to_open_without_evidence",
    "reserved_to_open_with_seller_evidence",
    "seller_without_stable_id",
}
ANSWER_KEYS_IN_FIXTURES = {
    "ambiguous",
    "clear_sold_evidence",
    "expected_seller_key",
    "expected_status",
    "labelled_visible",
    "question",
    "trap",
}
PRICE_ANSWER_KEYS = {
    "price_accuracy",
    "price_correct",
    "expected_price",
    "expected_amount",
    "price_error",
}


def read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: cannot load JSON: {exc}")
        return None


def safe_child(root: Path, relative: str, errors: list[str], context: str) -> Path | None:
    if not isinstance(relative, str) or not relative:
        errors.append(f"{context}: expected a non-empty fixture path")
        return None
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        errors.append(f"{context}: fixture path escapes corpus: {relative}")
        return None
    return path


def validate_observation(
    path: Path, document: Any, validator: Draft202012Validator, errors: list[str]
) -> bool:
    failures = observation_problems(validator, document)
    errors.extend(f"{path}: observation schema/invariant: {failure}" for failure in failures)
    return not failures


def scan_fixture_answers(directory: Path, errors: list[str]) -> None:
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() == "expected.json" or path.name.lower().startswith("expected-"):
            errors.append(f"{path}: answer file belongs only in labels.json")
            continue
        if path.suffix.lower() != ".json":
            continue
        document = read_json(path, errors)
        if document is None:
            continue
        pending = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                forbidden = sorted(ANSWER_KEYS_IN_FIXTURES.intersection(value))
                if forbidden:
                    errors.append(f"{path}: answer field(s) inside fixture: {', '.join(forbidden)}")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def case_label(case: dict[str, Any]) -> str:
    if case["kind"] == "discovery":
        return (
            f"visible={str(case['labelled_visible']).lower()}; "
            f"readable={str(case['readable']).lower()}"
        )
    seller_key = case["expected_seller_key"] or "ไม่ทราบ key"
    stable = "มี stable ID" if case["seller_has_stable_id"] else "ไม่มี stable ID"
    sold = "ใช่" if case["clear_sold_evidence"] else "ไม่ใช่"
    trap = case["trap"] or "ไม่มี"
    return (
        f"status={case['expected_status']}; seller={seller_key} ({stable}); "
        f"หลักฐาน sold ชัด={sold}; trap={trap}"
    )


LABEL_STATUSES = ("agent-draft", "agent-reviewed-owner-delegated", "owner-confirmed")
STATUS_LINES = {
    "agent-draft": "สถานะป้าย: agent-draft · ผู้ดูแลต้องตรวจและยืนยันก่อนใช้เป็น answer key",
    "agent-reviewed-owner-delegated": (
        "สถานะป้าย: agent-reviewed-owner-delegated · reviewer เอเจนต์อิสระตรวจทุกแถวตามที่ผู้ดูแล"
        "มอบหมายใน Issue #72; ยังไม่ใช่ป้ายที่คนตรวจ"
    ),
    "owner-confirmed": "สถานะป้าย: owner-confirmed · ผู้ดูแลยืนยันใน Issue #72",
}


def review_markdown(labels: dict[str, Any]) -> str:
    lines = [
        "# A9b — ใบตรวจป้ายร่าง",
        "",
        STATUS_LINES[labels["label_status"]],
        "",
        "| case_id | fixture path | สิ่งที่ fixture แสดง | label | เหตุผลตาม SRS/สัญญา | "
        "ambiguous + คำถาม |",
        "|---|---|---|---|---|---|",
    ]
    for case in labels["cases"]:
        if case["kind"] == "discovery":
            fixture_path = f"{case['feed']}#{case['run_label']}/cards/{case['card_index']}"
        else:
            fixture_path = f"{case['scenario']}/{case['final_round']} · {case['item_key']}"
        ambiguity = "ใช่ — " + case["question"] if case["ambiguous"] else "ไม่"
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    case["case_id"],
                    fixture_path,
                    case["description_th"],
                    case_label(case),
                    case["reason"],
                    ambiguity,
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def validate_corpus(corpus: Path, compare_review: bool = True) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    summary: dict[str, Any] = {}
    labels_path = corpus / "labels.json"
    labels = read_json(labels_path, errors)
    if not isinstance(labels, dict):
        return errors, summary

    if labels.get("corpus") != "A9b":
        errors.append(f"{labels_path}: corpus must be A9b")
    if labels.get("version") != 1:
        errors.append(f"{labels_path}: version must be 1")
    if labels.get("label_status") not in LABEL_STATUSES:
        errors.append(
            f"{labels_path}: label_status must be one of {LABEL_STATUSES}; "
            "only the owner's comment in #72 moves it past agent-draft"
        )
    if labels.get("drafted_by") != "gpt-6-luna":
        errors.append(f"{labels_path}: drafted_by must be gpt-6-luna")
    cases = labels.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{labels_path}: cases must be an array")
        return errors, summary

    feed_schema = read_json(FEED_SCHEMA, errors)
    observation_schema = read_json(OBSERVATION_SCHEMA, errors)
    if not isinstance(feed_schema, dict) or not isinstance(observation_schema, dict):
        return errors, summary
    Draft202012Validator.check_schema(feed_schema)
    Draft202012Validator.check_schema(observation_schema)
    feed_validator = Draft202012Validator(
        feed_schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    observation_validator = Draft202012Validator(observation_schema)

    discovery_root = corpus / "discovery"
    status_root = corpus / "status"
    if not discovery_root.is_dir():
        errors.append(f"{discovery_root}: missing discovery directory")
    if not status_root.is_dir():
        errors.append(f"{status_root}: missing status directory")
    discovery_dirs = (
        sorted(p for p in discovery_root.iterdir() if p.is_dir()) if discovery_root.is_dir() else []
    )
    status_dirs = (
        sorted(p for p in status_root.iterdir() if p.is_dir()) if status_root.is_dir() else []
    )

    feed_cards: dict[tuple[str, str, int], dict[str, Any]] = {}
    feed_runs: dict[str, list[dict[str, Any]]] = {}
    all_envelope_paths: set[Path] = set()
    for directory in discovery_dirs:
        if not FEED_DIR_RE.fullmatch(directory.name):
            errors.append(f"{directory}: invalid discovery directory name")
        if not (directory / "README.md").is_file():
            errors.append(f"{directory}: missing README.md")
        envelope_dir = directory / "envelopes"
        if not envelope_dir.is_dir():
            errors.append(f"{directory}: missing envelopes directory")
        feed_path = directory / "feed.json"
        feed = read_json(feed_path, errors)
        if feed is None:
            continue
        schema_failures = sorted(
            feed_validator.iter_errors(feed), key=lambda failure: str(failure.path)
        )
        errors.extend(f"{feed_path}: feed schema: {failure.message}" for failure in schema_failures)
        if schema_failures or not isinstance(feed, dict):
            continue
        relative_feed = feed_path.relative_to(corpus).as_posix()
        feed_runs[relative_feed] = feed["runs"]
        run_labels: set[str] = set()
        for run in feed["runs"]:
            run_label = run["label"]
            if run_label in run_labels:
                errors.append(f"{feed_path}: duplicate run label {run_label}")
            run_labels.add(run_label)
            for index, card in enumerate(run["cards"]):
                key = (relative_feed, run_label, index)
                feed_cards[key] = {"card": card, "run": run, "directory": directory}
                envelope_name = card["envelope"]
                if envelope_name is None:
                    continue
                envelope_path = (directory / envelope_name).resolve()
                try:
                    envelope_path.relative_to(directory.resolve())
                except ValueError:
                    errors.append(
                        f"{feed_path}: envelope path escapes feed directory: {envelope_name}"
                    )
                    continue
                if not envelope_path.is_file():
                    errors.append(f"{feed_path}: missing envelope {envelope_name}")
                    continue
                all_envelope_paths.add(envelope_path)
                envelope = read_json(envelope_path, errors)
                if envelope is None:
                    continue
                if validate_observation(envelope_path, envelope, observation_validator, errors):
                    payload = envelope["payload"]
                    if payload["source_post_key"] != card["source_post_key"]:
                        errors.append(f"{envelope_path}: payload post key differs from feed card")
                    if payload["group_key"] != feed["group_key"]:
                        errors.append(f"{envelope_path}: payload group key differs from feed")
                    if envelope["job_kind"] != "discover":
                        errors.append(
                            f"{envelope_path}: discovery envelope must use job_kind discover"
                        )
                    if envelope["page_check"]["sort"] != run["sort"]:
                        errors.append(f"{envelope_path}: page_check.sort differs from feed run")
        actual_envelopes = set(envelope_dir.glob("*.json")) if envelope_dir.is_dir() else set()
        unreferenced = actual_envelopes - all_envelope_paths
        for path in sorted(unreferenced):
            errors.append(f"{path}: envelope is not referenced by a feed card")
        scan_fixture_answers(directory, errors)

    status_histories: dict[str, dict[str, Any]] = {}
    for directory in status_dirs:
        if not STATUS_DIR_RE.fullmatch(directory.name):
            errors.append(f"{directory}: invalid status directory name")
        if not (directory / "README.md").is_file():
            errors.append(f"{directory}: missing README.md")
        rounds_dir = directory / "rounds"
        if not rounds_dir.is_dir():
            errors.append(f"{directory}: missing rounds directory")
            continue
        rounds: dict[str, dict[str, Any]] = {}
        round_paths = sorted(rounds_dir.glob("*.json"))
        for round_path in round_paths:
            if not ROUND_RE.fullmatch(round_path.name):
                errors.append(f"{round_path}: invalid round filename")
                continue
            document = read_json(round_path, errors)
            if document is None:
                continue
            if validate_observation(round_path, document, observation_validator, errors):
                rounds[round_path.name] = document
        if not rounds:
            errors.append(f"{directory}: no valid observation rounds")
            continue
        ordered = sorted(rounds.items(), key=lambda entry: entry[1]["payload"]["observed_at"])
        post_keys = {document["payload"]["source_post_key"] for document in rounds.values()}
        group_keys = {document["payload"]["group_key"] for document in rounds.values()}
        if len(post_keys) != 1:
            errors.append(f"{directory}: post key changes across rounds")
        if len(group_keys) != 1:
            errors.append(f"{directory}: group key changes across rounds")
        status_histories[f"status/{directory.name}"] = {
            "directory": directory,
            "rounds": rounds,
            "final_name": ordered[-1][0],
            "final": ordered[-1][1],
        }
        scan_fixture_answers(directory, errors)

    case_ids: set[str] = set()
    discovery_cases: list[dict[str, Any]] = []
    status_cases: list[dict[str, Any]] = []
    seen_feed_cards: set[tuple[str, str, int]] = set()
    seen_status_items: set[tuple[str, str, str]] = set()
    trap_counts: Counter[str] = Counter()
    for index, case in enumerate(cases):
        context = f"{labels_path}: cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{context}: each case must be an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{context}: missing case_id")
        elif case_id in case_ids:
            errors.append(f"{context}: duplicate case_id {case_id}")
        else:
            case_ids.add(case_id)
        if case.get("kind") not in {"discovery", "status"}:
            errors.append(f"{context}: kind must be discovery or status")
            continue
        price_fields = sorted(PRICE_ANSWER_KEYS.intersection(case))
        if price_fields:
            errors.append(
                f"{context}: price-accuracy labels are outside A9b: {', '.join(price_fields)}"
            )
        if not isinstance(case.get("description_th"), str) or not case["description_th"].strip():
            errors.append(f"{context}: description_th must contain a one-line Thai description")
        elif not re.search(r"[\u0e00-\u0e7f]", case["description_th"]):
            errors.append(f"{context}: description_th must be in Thai")
        if not isinstance(case.get("reason"), str) or not case["reason"].strip():
            errors.append(f"{context}: reason must identify the SRS/contract rule")
        elif "SRS" not in case["reason"] or "AT-0" not in case["reason"]:
            errors.append(f"{context}: reason must cite SRS and an AT heading")
        elif case["kind"] == "discovery" and "discovery-queue.md §4" not in case["reason"]:
            errors.append(f"{context}: discovery reason must cite discovery-queue.md §4")
        elif case["kind"] == "status" and "item-status-and-watches.md §2" not in case["reason"]:
            errors.append(f"{context}: status reason must cite item-status-and-watches.md §2")
        if not isinstance(case.get("ambiguous"), bool):
            errors.append(f"{context}: ambiguous must be a boolean")
        elif case["ambiguous"] and not (
            isinstance(case.get("question"), str) and case["question"].strip()
        ):
            errors.append(f"{context}: ambiguous case requires a reviewer question")

        if case["kind"] == "discovery":
            discovery_cases.append(case)
            required = (
                "feed",
                "run_label",
                "card_index",
                "post_key",
                "labelled_visible",
                "readable",
            )
            for field in required:
                if field not in case:
                    errors.append(f"{context}: missing {field}")
            if not isinstance(case.get("card_index"), int) or isinstance(
                case.get("card_index"), bool
            ):
                errors.append(f"{context}: card_index must be an integer")
                continue
            if not isinstance(case.get("labelled_visible"), bool) or not isinstance(
                case.get("readable"), bool
            ):
                errors.append(f"{context}: labelled_visible and readable must be booleans")
                continue
            feed_relative = case.get("feed")
            if safe_child(corpus, feed_relative, errors, context) is None:
                continue
            target = (feed_relative, case.get("run_label"), case["card_index"])
            card_record = feed_cards.get(target)
            if card_record is None:
                errors.append(f"{context}: no matching feed card at {target}")
                continue
            if target in seen_feed_cards:
                errors.append(f"{context}: feed card has more than one label row")
            seen_feed_cards.add(target)
            card = card_record["card"]
            if case.get("post_key") != card["source_post_key"]:
                errors.append(f"{context}: post_key differs from referenced feed card")
            if card["parse"] == "failed" and (
                case["post_key"] is not None or case["readable"] or case["labelled_visible"]
            ):
                errors.append(
                    f"{context}: unparseable card must be unreadable "
                    "and outside the visible denominator"
                )
            run = card_record["run"]
            if run["access"] != "ok" and (case["readable"] or case["labelled_visible"]):
                errors.append(
                    f"{context}: inaccessible page cards must be unreadable "
                    "and outside the denominator"
                )
            if run["load_limit"] is not None and case["card_index"] >= run["load_limit"]:
                if case["labelled_visible"] or case["readable"]:
                    errors.append(
                        f"{context}: card after load_limit must be unreadable and outside scope"
                    )
        else:
            status_cases.append(case)
            required = (
                "scenario",
                "final_round",
                "post_key",
                "item_key",
                "expected_seller_key",
                "seller_has_stable_id",
                "expected_status",
                "clear_sold_evidence",
                "trap",
            )
            for field in required:
                if field not in case:
                    errors.append(f"{context}: missing {field}")
            if case.get("expected_status") not in EXPECTED_STATUSES:
                errors.append(f"{context}: expected_status must use open/reserved/sold/unknown")
            if not isinstance(case.get("seller_has_stable_id"), bool):
                errors.append(f"{context}: seller_has_stable_id must be a boolean")
            if not isinstance(case.get("clear_sold_evidence"), bool):
                errors.append(f"{context}: clear_sold_evidence must be a boolean")
            if case.get("clear_sold_evidence") and case.get("expected_status") != "sold":
                errors.append(f"{context}: clear_sold_evidence requires expected_status sold")
            seller_key = case.get("expected_seller_key")
            if seller_key is not None and not isinstance(seller_key, str):
                errors.append(f"{context}: expected_seller_key must be a string or null")
            trap = case.get("trap")
            if trap is not None and trap not in TRAPS:
                errors.append(f"{context}: unsupported trap {trap}")
            if trap is not None:
                trap_counts[trap] += 1
            scenario_relative = case.get("scenario")
            if safe_child(corpus, scenario_relative, errors, context) is None:
                continue
            history = status_histories.get(scenario_relative)
            if history is None:
                errors.append(f"{context}: status history does not exist: {scenario_relative}")
                continue
            final_payload = history["final"]["payload"]
            if case.get("final_round") != f"rounds/{history['final_name']}":
                errors.append(f"{context}: final_round must identify the latest observation file")
            if case.get("post_key") != final_payload["source_post_key"]:
                errors.append(f"{context}: post_key differs from the final observation")
            final_seller = final_payload.get("seller")
            final_seller_key = (
                final_seller.get("external_actor_key") if isinstance(final_seller, dict) else None
            )
            if case.get("expected_seller_key") != final_seller_key:
                errors.append(f"{context}: expected_seller_key differs from the final post seller")
            if case.get("seller_has_stable_id") is not (final_seller_key is not None):
                errors.append(f"{context}: seller_has_stable_id differs from the final post seller")
            item_keys = {
                item["source_item_key"]
                for item in final_payload.get("items", [])
                if item.get("source_item_key") is not None
            }
            if case.get("item_key") not in item_keys:
                errors.append(f"{context}: item_key is absent from the final observation")
            item_target = (scenario_relative, case.get("post_key"), case.get("item_key"))
            if item_target in seen_status_items:
                errors.append(f"{context}: final-round item has more than one label row")
            seen_status_items.add(item_target)

    for key in sorted(feed_cards):
        if key not in seen_feed_cards:
            errors.append(f"{key[0]}#{key[1]}/cards/{key[2]}: missing discovery label row")
    for scenario_relative, history in status_histories.items():
        final_items = {
            item["source_item_key"]
            for item in history["final"]["payload"].get("items", [])
            if item.get("source_item_key") is not None
        }
        labelled_items = {
            item_key
            for scenario, _post_key, item_key in seen_status_items
            if scenario == scenario_relative
        }
        for item_key in sorted(final_items - labelled_items):
            errors.append(f"{scenario_relative}: final item {item_key} has no status label row")

    visible_count = sum(case.get("labelled_visible") is True for case in discovery_cases)
    unreadable_count = sum(case.get("readable") is False for case in discovery_cases)
    feed_count = len({case.get("feed") for case in discovery_cases})
    sold_control_count = sum(
        case.get("clear_sold_evidence") is True and case.get("expected_status") == "sold"
        for case in status_cases
    )
    unknown_count = sum(case.get("expected_status") == "unknown" for case in status_cases)
    stable_id_count = sum(case.get("seller_has_stable_id") is True for case in status_cases)
    no_stable_id_count = sum(case.get("seller_has_stable_id") is False for case in status_cases)
    ambiguous_count = sum(case.get("ambiguous") is True for case in cases if isinstance(case, dict))
    summary.update(
        {
            "discovery_rows": len(discovery_cases),
            "discovery_feeds": feed_count,
            "visible_rows": visible_count,
            "unreadable_rows": unreadable_count,
            "status_rows": len(status_cases),
            "status_histories": len(status_histories),
            "sold_controls": sold_control_count,
            "unknown_status": unknown_count,
            "stable_id_rows": stable_id_count,
            "no_stable_id_rows": no_stable_id_count,
            "ambiguous_rows": ambiguous_count,
            "total_rows": len(discovery_cases) + len(status_cases),
            "trap_counts": trap_counts,
        }
    )

    if len(discovery_cases) < 40:
        errors.append(f"A9b minimum: discovery rows {len(discovery_cases)} < 40")
    if feed_count < 8:
        errors.append(f"A9b minimum: discovery feeds {feed_count} < 8")
    if visible_count < 30:
        errors.append(f"A9b minimum: labelled_visible rows {visible_count} < 30")
    if unreadable_count < 5:
        errors.append(f"A9b minimum: unreadable discovery rows {unreadable_count} < 5")
    if len(status_cases) < 60:
        errors.append(f"A9b minimum: status rows {len(status_cases)} < 60")
    if len(status_histories) < 25:
        errors.append(f"A9b minimum: status histories {len(status_histories)} < 25")
    total_rows = len(discovery_cases) + len(status_cases)
    if total_rows < 100:
        errors.append(f"A9b minimum: total label rows {total_rows} < 100")
    if sold_control_count < 20:
        errors.append(f"A9b minimum: clear sold controls {sold_control_count} < 20")
    if unknown_count < 5:
        errors.append(f"A9b minimum: unknown status rows {unknown_count} < 5")
    for trap in sorted(TRAPS):
        if trap_counts[trap] < 3:
            errors.append(f"A9b minimum: trap {trap} has {trap_counts[trap]} rows, needs 3")

    all_runs = [run for runs in feed_runs.values() for run in runs]
    has_pinned = any(entry["card"]["pinned"] for entry in feed_cards.values())
    has_missing_sort = any(run["sort"] == "unverified" for run in all_runs)
    has_reordering = False
    has_duplicate = False
    has_stop_before_overlap = False
    for feed_relative, runs in feed_runs.items():
        run_orders = [
            [card["source_post_key"] for card in run["cards"] if card["source_post_key"]]
            for run in runs
        ]
        for left_index, left in enumerate(run_orders):
            for right in run_orders[left_index + 1 :]:
                common = set(left).intersection(right)
                if common and [key for key in left if key in common] != [
                    key for key in right if key in common
                ]:
                    has_reordering = True
        directory = next(
            (entry["directory"] for key, entry in feed_cards.items() if key[0] == feed_relative),
            None,
        )
        if directory and "stop-before-overlap" in directory.name:
            has_stop_before_overlap = any(
                run["load_limit"] is not None and run["load_limit"] < len(run["cards"])
                for run in runs
            )
        for run in runs:
            keys = [card["source_post_key"] for card in run["cards"] if card["source_post_key"]]
            if len(keys) != len(set(keys)):
                has_duplicate = True
    if not has_pinned:
        errors.append("A9b discovery mix: missing pinned cards")
    if not has_reordering:
        errors.append("A9b discovery mix: missing reordered stable keys across feed batches")
    if not has_missing_sort:
        errors.append("A9b discovery mix: missing unverified sort run")
    if not has_stop_before_overlap:
        errors.append("A9b discovery mix: missing stop-before-overlap feed with load_limit")
    if not has_duplicate:
        errors.append("A9b discovery mix: missing duplicate stable cards")
    summary.update(
        {
            "pinned": has_pinned,
            "reordering": has_reordering,
            "missing_sort": has_missing_sort,
            "stop_before_overlap": has_stop_before_overlap,
            "duplicate_cards": has_duplicate,
        }
    )

    for case in cases:
        if isinstance(case, dict) and case.get("kind") == "discovery":
            feed = case.get("feed")
            run_label = case.get("run_label")
            card_index = case.get("card_index")
            if (
                isinstance(feed, str)
                and isinstance(run_label, str)
                and isinstance(card_index, int)
                and (feed, run_label, card_index) in feed_cards
            ):
                card = feed_cards[(feed, run_label, card_index)]["card"]
                if card["pinned"] and case.get("labelled_visible") is False:
                    reason = str(case.get("reason", "")).lower()
                    if "pinned" not in reason and "ปักหมุด" not in reason:
                        errors.append(
                            f"{case.get('case_id')}: out-of-scope pinned card needs a pinned reason"
                        )

    review_path = corpus / "REVIEW.md"
    if compare_review:
        try:
            actual_review = review_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{review_path}: cannot read generated review sheet: {exc}")
        else:
            if actual_review != review_markdown(labels):
                errors.append(
                    f"{review_path}: out of date; run scripts/check_corpus.py --write-review"
                )
    return errors, summary


def print_counts(summary: dict[str, Any]) -> None:
    if not summary:
        return
    trap_counts = summary.get("trap_counts", Counter())
    print("A9b count table")
    print("| Category | Rows / units |")
    print("|---|---:|")
    print(f"| Discovery cases | {summary.get('discovery_rows', 0)} |")
    print(f"| Discovery feeds | {summary.get('discovery_feeds', 0)} |")
    print(f"| Discovery visible=true | {summary.get('visible_rows', 0)} |")
    print(f"| Discovery readable=false | {summary.get('unreadable_rows', 0)} |")
    print(f"| Status cases | {summary.get('status_rows', 0)} |")
    print(f"| Status histories | {summary.get('status_histories', 0)} |")
    print(f"| Clear sold controls | {summary.get('sold_controls', 0)} |")
    print(f"| Expected unknown | {summary.get('unknown_status', 0)} |")
    print(f"| Stable-ID seller rows | {summary.get('stable_id_rows', 0)} |")
    print(f"| No-stable-ID seller rows | {summary.get('no_stable_id_rows', 0)} |")
    print(f"| Ambiguous rows | {summary.get('ambiguous_rows', 0)} |")
    print(f"| Total label rows | {summary.get('total_rows', 0)} |")
    print("| Trap | Rows |")
    print("|---|---:|")
    for trap in sorted(TRAPS):
        print(f"| {trap} | {trap_counts[trap]} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the A9b synthetic acceptance corpus")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--write-review", action="store_true")
    args = parser.parse_args()
    corpus = args.corpus_dir.resolve()
    labels_path = corpus / "labels.json"
    labels: Any = None
    if args.write_review:
        try:
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"{labels_path}: cannot load labels for review generation: {exc}", file=sys.stderr
            )
            return 1
        if not isinstance(labels, dict) or not isinstance(labels.get("cases"), list):
            print(f"{labels_path}: expected an object with a cases array", file=sys.stderr)
            return 1
        (corpus / "REVIEW.md").write_text(review_markdown(labels), encoding="utf-8", newline="\n")
        print(f"Wrote {corpus / 'REVIEW.md'}")
    errors, summary = validate_corpus(corpus, compare_review=not args.write_review)
    print_counts(summary)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    status = json.loads(labels_path.read_text(encoding="utf-8"))["label_status"]
    print(f"A9b corpus OK; label_status={status}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
