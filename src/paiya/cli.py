"""Local JSON-lines CLI for A3 fixture group management."""

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from paiya.demo import seed_demo
from paiya.groups.errors import GroupError
from paiya.groups.service import add_group, list_groups, pause, resume, set_interval
from paiya.settings import get_database_engine
from paiya.watches.errors import WatchError
from paiya.watches.service import add_watch, list_watches, remove_watch, set_watch_hot


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _print_error("invalid_command", message)
        raise SystemExit(2)


def _print_error(code: str, message: str) -> None:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))


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
    return parser


def _execute(args: argparse.Namespace) -> object:
    if args.resource == "demo" and args.action == "seed":
        return seed_demo(get_database_engine())

    now = datetime.now(UTC)
    engine = get_database_engine()
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
    except GroupError as error:
        _print_error(error.code, error.message)
        return 1
    except WatchError as error:
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
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
