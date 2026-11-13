from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from paiya_collector.agent import EvidenceAgent, OllamaEvidenceAgent
from paiya_collector.browser_adapter import (
    LiveGroupConfig,
    RateLimitPause,
    capture_browser_feed,
    load_group_config,
    prune_expired_bundles,
)
from paiya_collector.browser_runtime import attach_dedicated_browser
from paiya_collector.html_adapter import utc_now
from paiya_collector.inbox import publish_bundle
from paiya_collector.runner import DiscoveryResult, run_discovery
from paiya_collector.state import (
    ensure_no_reparse_components,
    load_state,
    prune_expired_state,
    state_path,
    write_state,
)


def discover_browser(
    group_config_path: Path,
    *,
    profile_dir: Path,
    inbox_root: Path,
    state_dir: Path,
    run_id_arg: str | None = None,
    evidence_agent: EvidenceAgent | None = None,
    browser_attacher: Callable[..., object] | None = None,
    repository_root: Path | None = None,
    now: datetime | None = None,
    clear_manual_rate_limit_pause: bool = False,
) -> dict[str, object]:
    repo_root = repository_root or Path(__file__).resolve().parents[1]
    config = load_group_config(group_config_path, repository_root=repo_root)
    run_id = str(uuid.UUID(run_id_arg)) if run_id_arg else str(uuid.uuid4())
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)

    with group_run_lock(state_dir, config.group_key, run_id):
        state_file = state_path(state_dir, config.group_key)
        removed_runs = prune_expired_bundles(
            inbox_root,
            group_key=config.group_key,
            retention_days=config.retention_days,
            now=current,
        )
        expired_state = prune_expired_state(
            state_file,
            group_key=config.group_key,
            retention_days=config.retention_days,
            now=current,
            run_id=str(uuid.uuid4()),
        )
        state = load_state(state_file, config.group_key)

        previous_run = state.get("published_runs", {}).get(run_id)
        if isinstance(previous_run, dict):
            report = previous_run.get("report")
            bundle_path = Path(str(previous_run.get("bundle_path", "")))
            if isinstance(report, dict) and (bundle_path / "READY").is_file():
                replay = dict(report)
                replay["replayed"] = True
                return replay
            raise ValueError(
                f"run_id was already used and its published bundle is unavailable: {run_id}"
            )

        _check_rate_limit_pause(
            state_dir,
            config.group_key,
            now=current,
            clear_manual_pause=clear_manual_rate_limit_pause,
        )

        run_started = utc_now() if now is None else _format_utc(current)
        previous_keys = {key for key in state["published_keys"] if isinstance(key, str)}
        attacher = browser_attacher or attach_dedicated_browser
        try:
            with attacher(
                profile_dir,
                config=config,
                repository_root=repo_root,
            ) as page:
                page.navigate(config.group_url)
                feed = capture_browser_feed(
                    page,
                    config=config,
                    previously_published=previous_keys,
                    now=current,
                )
        except RateLimitPause as pause:
            _write_rate_limit_pause(state_dir, config.group_key, pause, run_id, current)
            raise

        result = run_discovery(
            feed,
            fixture_path=group_config_path,
            group_key=config.group_key,
            identity_scope=config.identity_scope,
            observed_at=run_started,
            page_size=config.page_size,
            scroll_budget=config.scroll_budget,
            time_budget_seconds=config.time_budget_seconds,
            overlap=config.overlap,
            previously_published=previous_keys,
            evidence_agent=evidence_agent,
        )
        ended_at = utc_now() if now is None else _format_utc(current)
        run_document = _run_document(config, run_id, run_started, ended_at, result)

        envelopes: list[dict[str, object]] = []
        asset_bytes: dict[str, bytes] = {}
        for card in result.parsed_cards:
            capture_id = str(uuid.uuid5(uuid.UUID(run_id), card.source_post_key))
            envelopes.append(
                {
                    "run_id": run_id,
                    "capture_id": capture_id,
                    "grant_id": config.grant_id,
                    "source_kind": "facebook_live",
                    "job_kind": "discover",
                    "page_check": {"session": feed.session_result, "sort": feed.sort_result},
                    "assets": card.assets,
                    "payload": card.payload,
                }
            )
            asset_bytes.update(card.asset_bytes)

        fingerprint = _input_fingerprint(
            config.group_key,
            result,
            None if evidence_agent is None else getattr(evidence_agent, "model", None),
        )
        bundle_path = publish_bundle(inbox_root, run_document, envelopes, asset_bytes)
        report = {
            "ok": True,
            "run_id": run_id,
            "bundle_path": str(bundle_path),
            "cards_seen": result.cards_seen,
            "cards_parsed": result.cards_parsed,
            "duplicate_cards_skipped": len(result.duplicate_skips),
            "duplicate_skips": list(result.duplicate_skips),
            "coverage": result.coverage,
            "stop_reason": result.stop_reason,
            "partial_reasons": list(result.partial_reasons),
            "error_code": result.error_code,
            "scroll_steps": result.scroll_steps,
            "retention_deleted_runs": list(removed_runs),
            "retention_expired_state": expired_state,
            "evidence_agent": (
                {"provider": "ollama-local", "model": getattr(evidence_agent, "model")}
                if isinstance(evidence_agent, OllamaEvidenceAgent)
                else None
            ),
        }

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
        published_runs = dict(next_state.get("published_runs", {}))
        published_runs[run_id] = {
            "published_at": ended_at,
            "input_fingerprint": fingerprint,
            "bundle_path": str(bundle_path),
            "report": report,
        }
        next_state["published_runs"] = published_runs
        write_state(state_file, next_state, run_id)
        _clear_expired_rate_limit_pause(state_dir, config.group_key, now=current)
        return report


def _run_document(
    config: LiveGroupConfig,
    run_id: str,
    started_at: str,
    ended_at: str,
    result: DiscoveryResult,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "group_key": config.group_key,
        "grant_id": config.grant_id,
        "job_kind": "discover",
        "adapter_version": config.adapter_version,
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


def _input_fingerprint(
    group_key: str,
    result: DiscoveryResult,
    agent_model: str | None,
) -> str:
    document = {
        "group_key": group_key,
        "agent_model": agent_model,
        "coverage": result.coverage,
        "stop_reason": result.stop_reason,
        "partial_reasons": list(result.partial_reasons),
        "duplicates": list(result.duplicate_skips),
        "cards": [
            {
                "payload": card.payload,
                "assets": card.assets,
            }
            for card in result.parsed_cards
        ],
    }
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def group_run_lock(state_dir: Path, group_key: str, run_id: str):
    @contextmanager
    def hold() -> Iterator[None]:
        ensure_no_reparse_components(state_dir, label="state directory")
        state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = state_dir / f".{group_key}.run.lock"
        content = json.dumps(
            {
                "group_key": group_key,
                "run_id": run_id,
                "process_id": os.getpid(),
                "started_at": utc_now(),
            },
            separators=(",", ":"),
        )
        try:
            with lock_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
        except FileExistsError as exc:
            raise ValueError("a Collector run is already active for this group") from exc
        try:
            yield
        finally:
            try:
                current = json.loads(lock_path.read_text(encoding="utf-8"))
                if current.get("run_id") == run_id:
                    lock_path.unlink()
            except (OSError, json.JSONDecodeError, AttributeError):
                pass

    return hold()


def _check_rate_limit_pause(
    state_dir: Path,
    group_key: str,
    *,
    now: datetime,
    clear_manual_pause: bool,
) -> None:
    pause_path = _rate_limit_path(state_dir, group_key)
    if not pause_path.exists():
        return
    try:
        state = json.loads(pause_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "local HTTP 429 pause state is unreadable; inspect it before retrying"
        ) from exc
    if not isinstance(state, dict) or state.get("group_key") != group_key:
        raise ValueError("local HTTP 429 pause state does not match the configured group")

    deadline = _parse_utc(state.get("retry_after_until"))
    if state.get("manual_resume_required") is True:
        if not clear_manual_pause:
            raise RateLimitPause(None, manual_resume_required=True)
        pause_path.unlink()
        return
    if deadline is not None and deadline > now:
        raise RateLimitPause(deadline)


def _write_rate_limit_pause(
    state_dir: Path,
    group_key: str,
    pause: RateLimitPause,
    run_id: str,
    now: datetime,
) -> None:
    deadline = pause.retry_after_until
    state = {
        "group_key": group_key,
        "retry_after_until": None if deadline is None else _format_utc(deadline),
        "manual_resume_required": pause.manual_resume_required,
        "recorded_at": _format_utc(now),
    }
    write_state(_rate_limit_path(state_dir, group_key), state, run_id)


def _clear_expired_rate_limit_pause(state_dir: Path, group_key: str, *, now: datetime) -> None:
    pause_path = _rate_limit_path(state_dir, group_key)
    if not pause_path.is_file():
        return
    try:
        state = json.loads(pause_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    deadline = _parse_utc(state.get("retry_after_until")) if isinstance(state, dict) else None
    if deadline is not None and deadline <= now:
        pause_path.unlink()


def _rate_limit_path(state_dir: Path, group_key: str) -> Path:
    return state_dir / f"{group_key}.rate-limit.json"


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
