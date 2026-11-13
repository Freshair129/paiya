from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from paiya_collector.agent import EvidenceAgent, OllamaEvidenceAgent
from paiya_collector.browser_adapter import (
    APPROVED_GROUP_KEY,
    APPROVED_RETENTION_DAYS,
    RateLimitPause,
    prune_expired_bundles,
)
from paiya_collector.browser_runtime import BrowserAdapterError
from paiya_collector.html_adapter import (
    AdapterError,
    ParsedCard,
    UnsupportedSourceCondition,
    parse_feed,
    utc_now,
)
from paiya_collector.inbox import publish_bundle
from paiya_collector.live import discover_browser, group_run_lock
from paiya_collector.runner import run_discovery
from paiya_collector.state import load_state, prune_expired_state, state_path, write_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paiya-collector")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser(
        "discover", help="discover posts from a local synthetic HTML fixture"
    )
    discover.add_argument("--fixture", type=Path, required=True)
    discover.add_argument("--group-config", type=Path, required=True)
    discover.add_argument("--inbox", type=Path, required=True)
    discover.add_argument("--run-id", help="UUID idempotency key; reuse it to retry the same run")
    discover.add_argument(
        "--state-dir",
        type=Path,
        help="local producer state directory (defaults beside group config)",
    )
    discover.add_argument(
        "--agent-model",
        help=(
            "optional local Ollama model for evidence-backed post extraction "
            "(default tag: mellum:latest)"
        ),
    )

    browser = commands.add_parser(
        "discover-browser",
        help="manually discover visible posts from the owner-approved dedicated Chrome profile",
    )
    browser.add_argument("--group-config", type=Path, required=True)
    browser.add_argument("--profile-dir", type=Path, required=True)
    browser.add_argument("--inbox", type=Path, required=True)
    browser.add_argument("--state-dir", type=Path, required=True)
    browser.add_argument("--run-id", help="UUID idempotency key; reuse it to replay the same run")
    browser.add_argument(
        "--confirm-live",
        action="store_true",
        required=True,
        help="confirm this manually started browser run against the locally mapped group",
    )
    browser.add_argument(
        "--clear-manual-rate-limit-pause",
        action="store_true",
        help="after waiting manually, clear a 429 pause that had no usable Retry-After header",
    )
    browser.add_argument(
        "--agent-model",
        help=(
            "optional local Ollama model for evidence-backed post extraction "
            "(default tag: mellum:latest)"
        ),
    )

    prune = commands.add_parser(
        "prune-expired",
        help=(
            "delete this approved group's ready inbox bundles after the seven-day retention period"
        ),
    )
    prune.add_argument("--inbox", type=Path, required=True)
    prune.add_argument("--state-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence_agent = (
            OllamaEvidenceAgent(args.agent_model) if getattr(args, "agent_model", None) else None
        )
        if args.command == "prune-expired":
            cleanup_id = str(uuid.uuid4())
            current = datetime.now(UTC)
            with group_run_lock(args.state_dir, APPROVED_GROUP_KEY, cleanup_id):
                removed = prune_expired_bundles(
                    args.inbox,
                    group_key=APPROVED_GROUP_KEY,
                    retention_days=APPROVED_RETENTION_DAYS,
                    now=current,
                )
                expired_state = prune_expired_state(
                    state_path(args.state_dir, APPROVED_GROUP_KEY),
                    group_key=APPROVED_GROUP_KEY,
                    retention_days=APPROVED_RETENTION_DAYS,
                    now=current,
                    run_id=cleanup_id,
                )
            report = {
                "ok": True,
                "group_key": APPROVED_GROUP_KEY,
                "retention_days": APPROVED_RETENTION_DAYS,
                "removed_runs": list(removed),
                "expired_state": expired_state,
            }
        elif args.command == "discover-browser":
            report = discover_browser(
                args.group_config,
                profile_dir=args.profile_dir,
                inbox_root=args.inbox,
                state_dir=args.state_dir,
                run_id_arg=args.run_id,
                evidence_agent=evidence_agent,
                clear_manual_rate_limit_pause=args.clear_manual_rate_limit_pause,
            )
        else:
            report = discover(
                args.fixture,
                args.group_config,
                args.inbox,
                args.run_id,
                args.state_dir,
                evidence_agent=evidence_agent,
            )
    except RateLimitPause as exc:
        _emit(
            {
                "ok": False,
                "paused": True,
                "reason": "http_429",
                "retry_after_until": (
                    None
                    if exc.retry_after_until is None
                    else exc.retry_after_until.isoformat().replace("+00:00", "Z")
                ),
                "manual_resume_required": exc.manual_resume_required,
                "checkpoint_unchanged": True,
            }
        )
        return 2
    except UnsupportedSourceCondition as exc:
        _emit(
            {
                "ok": False,
                "paused": True,
                "reason": "rate_limited_mapping_unavailable",
                "message": str(exc),
                "checkpoint_unchanged": True,
            }
        )
        return 2
    except (BrowserAdapterError, AdapterError, OSError, ValueError, json.JSONDecodeError) as exc:
        _emit({"ok": False, "error": str(exc)})
        return 2
    _emit(report)
    return 0


def discover(
    fixture_path: Path,
    group_config_path: Path,
    inbox_root: Path,
    run_id_arg: str | None = None,
    state_dir_arg: Path | None = None,
    *,
    evidence_agent: EvidenceAgent | None = None,
) -> dict[str, object]:
    config = json.loads(group_config_path.read_text(encoding="utf-8"))
    group_key = _required_string(config, "group_key")
    grant_id = _required_string(config, "grant_id")
    uuid.UUID(grant_id)
    adapter_version = _required_string(config, "adapter_version")
    identity_scope = _required_string(config, "identity_scope")
    page_size = int(config.get("page_size", 5))
    overlap = int(config.get("overlap", 3))
    scroll_budget = int(config.get("scroll_budget", 30))
    time_budget_seconds = int(config.get("time_budget_seconds", 120))
    run_id = str(uuid.UUID(run_id_arg)) if run_id_arg else str(uuid.uuid4())

    started_at = utc_now()
    feed = parse_feed(fixture_path)
    state_dir = state_dir_arg or (group_config_path.parent / ".collector-state")
    checkpoint_file = state_path(state_dir, group_key)
    current = datetime.now(UTC)
    removed_runs = prune_expired_bundles(
        inbox_root,
        group_key=group_key,
        retention_days=APPROVED_RETENTION_DAYS,
        now=current,
    )
    expired_state = prune_expired_state(
        checkpoint_file,
        group_key=group_key,
        retention_days=APPROVED_RETENTION_DAYS,
        now=current,
        run_id=str(uuid.uuid4()),
    )
    state = load_state(checkpoint_file, group_key)
    previous_keys = {key for key in state["published_keys"] if isinstance(key, str)}
    result = run_discovery(
        feed,
        fixture_path=fixture_path,
        group_key=group_key,
        identity_scope=identity_scope,
        observed_at=started_at,
        page_size=page_size,
        scroll_budget=scroll_budget,
        time_budget_seconds=time_budget_seconds,
        overlap=overlap,
        previously_published=previous_keys,
        evidence_agent=evidence_agent,
    )

    agent_model = evidence_agent.model if isinstance(evidence_agent, OllamaEvidenceAgent) else None
    input_fingerprint = _input_fingerprint(
        fixture_path, group_config_path, result.parsed_cards, agent_model
    )
    published_runs = dict(state.get("published_runs", {}))
    previous_run = published_runs.get(run_id)
    if previous_run is not None:
        if (
            isinstance(previous_run, dict)
            and previous_run.get("input_fingerprint") == input_fingerprint
            and isinstance(previous_run.get("report"), dict)
        ):
            report = dict(previous_run["report"])
            bundle = Path(str(report.get("bundle_path", "")))
            if (bundle / "READY").is_file():
                report["replayed"] = True
                return report
        raise ValueError(
            f"run_id was already published with different or unverifiable input: {run_id}"
        )

    ended_at = utc_now()
    run_document: dict[str, object] = {
        "run_id": run_id,
        "group_key": group_key,
        "grant_id": grant_id,
        "job_kind": "discover",
        "adapter_version": adapter_version,
        "started_at": started_at,
        "ended_at": ended_at,
        "session_result": result.session_result,
        "sort_result": result.sort_result,
        "coverage": result.coverage,
        "partial_reasons": list(result.partial_reasons),
        "stop_reason": result.stop_reason,
        "cards_seen": result.cards_seen,
        "cards_parsed": result.cards_parsed,
        "duplicate_cards_skipped": len(result.duplicate_skips),
        "duplicate_skips": list(result.duplicate_skips),
        "scroll_steps": result.scroll_steps,
        "error_code": result.error_code,
    }

    envelopes: list[dict[str, object]] = []
    all_asset_bytes: dict[str, bytes] = {}
    for card in result.parsed_cards:
        capture_id = str(uuid.uuid5(uuid.UUID(run_id), card.source_post_key))
        envelopes.append(
            {
                "run_id": run_id,
                "capture_id": capture_id,
                "grant_id": grant_id,
                "source_kind": "facebook_live",
                "job_kind": "discover",
                "page_check": {"session": feed.session_result, "sort": feed.sort_result},
                "assets": card.assets,
                "payload": card.payload,
            }
        )
        all_asset_bytes.update(card.asset_bytes)

    bundle_path = publish_bundle(inbox_root, run_document, envelopes, all_asset_bytes)
    next_state = copy.deepcopy(state)
    published_keys = list(next_state["published_keys"])
    key_seen_at = dict(next_state["published_key_seen_at"])
    for card in result.parsed_cards:
        if card.source_post_key not in published_keys:
            published_keys.append(card.source_post_key)
            key_seen_at[card.source_post_key] = ended_at
    next_state["published_keys"] = published_keys
    next_state["published_key_seen_at"] = key_seen_at
    if result.coverage == "complete":
        next_state["checkpoint"] = {
            "run_id": run_id,
            "top_keys": list(result.checkpoint_top_keys),
            "advanced_at": ended_at,
        }
    report: dict[str, object] = {
        "ok": True,
        "run_id": run_id,
        "bundle_path": str(bundle_path),
        "cards_seen": result.cards_seen,
        "cards_parsed": result.cards_parsed,
        "duplicate_cards_skipped": len(result.duplicate_skips),
        "duplicate_skips": list(result.duplicate_skips),
        "coverage": result.coverage,
        "stop_reason": result.stop_reason,
        "partial_reasons": run_document["partial_reasons"],
        "error_code": result.error_code,
        "scroll_steps": result.scroll_steps,
        "retention_deleted_runs": list(removed_runs),
        "retention_expired_state": expired_state,
        "evidence_agent": (
            {"provider": "ollama-local", "model": agent_model} if agent_model is not None else None
        ),
    }
    published_runs[run_id] = {
        "published_at": ended_at,
        "input_fingerprint": input_fingerprint,
        "bundle_path": str(bundle_path),
        "report": report,
    }
    next_state["published_runs"] = published_runs
    write_state(checkpoint_file, next_state, run_id)
    return report


def _input_fingerprint(
    fixture_path: Path,
    group_config_path: Path,
    cards: tuple[ParsedCard, ...],
    agent_model: str | None,
) -> str:
    digest = hashlib.sha256()
    digest.update((agent_model or "deterministic").encode("utf-8"))
    for path in (fixture_path, group_config_path):
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    for card in cards:
        source_key = card.source_post_key.encode("utf-8")
        digest.update(len(source_key).to_bytes(8, "big"))
        digest.update(source_key)
        for staging_key, content in sorted(card.asset_bytes.items()):
            key_bytes = staging_key.encode("utf-8")
            digest.update(len(key_bytes).to_bytes(8, "big"))
            digest.update(key_bytes)
            digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _required_string(config: dict[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"group config requires non-empty {key}")
    return value.strip()


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
