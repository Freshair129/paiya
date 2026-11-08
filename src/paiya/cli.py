"""Local JSON-lines CLI for A3 fixture group management."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from paiya.backup.errors import BackupError
from paiya.backup.service import backup_status, create_backup, restore_backup, verify_backup
from paiya.demo import seed_demo
from paiya.extract.service import rebuild_extractions
from paiya.grants.errors import GrantError
from paiya.grants.service import revoke_grant
from paiya.groups.errors import GroupError
from paiya.groups.service import add_group, list_groups, pause, resume, set_interval
from paiya.purge.errors import PurgeError
from paiya.purge.service import replay_purge_log, run_purge, validate_scope_format
from paiya.purge.startup import check_purge_log
from paiya.settings import get_database_engine
from paiya.watches.errors import WatchError
from paiya.watches.service import add_watch, list_watches, remove_watch, set_watch_hot


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _print_error("invalid_command", message)
        raise SystemExit(2)


def _print_error(code: str, message: str, details: dict[str, object] | None = None) -> None:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error.update(details)
    print(json.dumps({"ok": False, "error": error}))


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="python -m paiya.cli")
    commands = parser.add_subparsers(
        dest="resource", required=True, parser_class=_JsonArgumentParser
    )
    group = commands.add_parser("groups", help="manage polling groups")
    actions = group.add_subparsers(dest="action", required=True, parser_class=_JsonArgumentParser)

    add = actions.add_parser("add", help="add a synthetic fixture group")
    add.add_argument("group_key")
    add.add_argument("--poll-minutes", type=int, choices=(30, 60), required=True)

    interval = actions.add_parser("set-interval", help="change a group's polling interval")
    interval.add_argument("group_id", type=UUID)
    interval.add_argument("poll_minutes", type=int, choices=(30, 60))

    for name, help_text in (("pause", "pause a group"), ("resume", "resume a group")):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("group_id", type=UUID)
    actions.add_parser("list", help="list groups")

    watch = commands.add_parser("watches", help="manage post watches")
    watch_actions = watch.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    for name, help_text in (
        ("add", "watch a post"),
        ("remove", "remove a post watch"),
        ("hot", "open a 24-hour accelerated revisit window"),
    ):
        action = watch_actions.add_parser(name, help=help_text)
        action.add_argument("post_id", type=UUID)
    watch_actions.add_parser("list", help="list active-grant post watches")

    demo = commands.add_parser("demo", help="manage the synthetic mobile demo data")
    demo_actions = demo.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    demo_actions.add_parser("seed", help="seed the synthetic mobile demo group and posts")

    grant = commands.add_parser("grant", help="manage source grants")
    grant_actions = grant.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    revoke = grant_actions.add_parser("revoke", help="revoke a synthetic source grant")
    revoke.add_argument("grant_id")

    purge = commands.add_parser("purge", help="purge a synthetic grant or post scope")
    purge_actions = purge.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    purge_run = purge_actions.add_parser("run", help="purge one synthetic data scope")
    purge_run.add_argument("--scope", required=True)
    purge_actions.add_parser("replay", help="replay pending synthetic purge commands")

    backup = commands.add_parser("backup", help="create, verify, restore or inspect a backup")
    backup_actions = backup.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    backup_actions.add_parser("create", help="create a complete backup set")
    verify = backup_actions.add_parser("verify", help="verify a backup set")
    verify.add_argument("--set", dest="set_path", required=True)
    restore = backup_actions.add_parser("restore", help="restore a backup set into empty targets")
    restore.add_argument("--set", dest="set_path", required=True)
    restore.add_argument("--target-database-url", required=True)
    restore.add_argument("--target-media-root", required=True)
    backup_actions.add_parser("status", help="show the latest successful backup and latest failure")

    extract = commands.add_parser("extract", help="rebuild rule-based extraction tags")
    extract_actions = extract.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    extract_actions.add_parser("rebuild", help="add missing tags and rebuild item projections")

    enrich = commands.add_parser("enrich", help="run or inspect local-model enrichment")
    enrich_actions = enrich.add_subparsers(
        dest="action", required=True, parser_class=_JsonArgumentParser
    )
    for name, help_text in (
        ("run-once", "process one due enrichment job"),
        ("run", "keep processing enrichment jobs"),
        ("status", "show enrichment queue status"),
    ):
        enrich_actions.add_parser(name, help=help_text)
    return parser


def _execute(args: argparse.Namespace) -> object:
    if args.resource == "backup":
        if args.action == "verify":
            return verify_backup(Path(args.set_path))
        engine = get_database_engine()
        if args.action == "create":
            return create_backup(engine)
        if args.action == "restore":
            return restore_backup(
                engine,
                set_path=Path(args.set_path),
                target_database_url=args.target_database_url,
                target_media_root=Path(args.target_media_root),
            )
        if args.action == "status":
            return backup_status(engine)
    if args.resource == "demo" and args.action == "seed":
        return seed_demo(get_database_engine())
    if args.resource == "purge" and args.action == "run":
        validate_scope_format(args.scope)
        return run_purge(get_database_engine(), args.scope)
    if args.resource == "purge" and args.action == "replay":
        return replay_purge_log(get_database_engine())

    now = datetime.now(UTC)
    engine = get_database_engine()
    if args.resource == "extract" and args.action == "rebuild":
        return rebuild_extractions(engine)
    if args.resource == "grant" and args.action == "revoke":
        try:
            grant_id = UUID(args.grant_id)
        except ValueError as error:
            raise GrantError("scope_not_found", "The grant was not found") from error
        return revoke_grant(engine, grant_id)
    if args.resource == "enrich":
        from paiya.enrich.service import enrich_status, run_enrich_once, run_enrich_worker

        if args.action == "status":
            return enrich_status(engine, now=now)
        if args.action in {"run-once", "run"}:
            check_purge_log(engine)
        if args.action == "run-once":
            return run_enrich_once(engine, now=now) or {"state": "idle"}
        if args.action == "run":
            run_enrich_worker(engine)
            return {"state": "stopped"}

    with engine.connect() as conn:
        if args.resource == "watches":
            if args.action == "add":
                return add_watch(conn, now, args.post_id)
            if args.action == "remove":
                return remove_watch(conn, now, args.post_id)
            if args.action == "hot":
                return set_watch_hot(conn, now, args.post_id)
            if args.action == "list":
                return list_watches(conn, now)
        if args.action == "add":
            return add_group(conn, now, args.group_key, args.poll_minutes)
        if args.action == "set-interval":
            return set_interval(conn, now, args.group_id, args.poll_minutes)
        if args.action == "pause":
            return pause(conn, now, args.group_id)
        if args.action == "resume":
            return resume(conn, now, args.group_id)
        if args.action == "list":
            return list_groups(conn, now)
    raise ValueError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _execute(args)
    except BackupError as error:
        _print_error(error.code, error.message, error.details)
        return 1
    except GroupError as error:
        _print_error(error.code, error.message)
        return 1
    except WatchError as error:
        _print_error(error.code, error.message)
        return 1
    except GrantError as error:
        _print_error(error.code, error.message)
        return 1
    except PurgeError as error:
        _print_error(error.code, error.message)
        return 1
    except (RuntimeError, ValueError) as error:
        _print_error("invalid_command", str(error))
        return 1
    except OSError as error:
        _print_error("io_error", str(error))
        return 1
    except SQLAlchemyError:
        _print_error("database_error", "The database operation failed")
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": True, "result": {"state": "stopped"}}))
        return 0
    if args.resource == "purge" and args.action == "replay":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        replay_result = cast(dict[str, object], result)
        return 0 if replay_result.get("pending_count") == 0 else 1
    if args.resource == "purge":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        purge_result = cast(dict[str, object], result)
        return 0 if purge_result.get("state") == "succeeded" else 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
