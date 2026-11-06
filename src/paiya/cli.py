"""Local JSON-lines CLI for A3 fixture group management."""

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from paiya.groups.errors import GroupError
from paiya.groups.service import add_group, list_groups, pause, resume, set_interval
from paiya.settings import get_database_engine


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
    return parser


def _execute(args: argparse.Namespace) -> object:
    now = datetime.now(UTC)
    engine = get_database_engine()
    with engine.connect() as conn:
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
    except (RuntimeError, ValueError) as error:
        _print_error("invalid_command", str(error))
        return 1
    except SQLAlchemyError:
        _print_error("database_error", "The database operation failed")
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
