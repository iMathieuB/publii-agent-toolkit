"""Command line interface, shaped for an agent as much as for a person.

Every subcommand reads JSON on stdin or from an argument and writes one JSON
object on stdout. Exit status is 0 when ``ok`` is true and 1 when it is not, so
a caller can branch on either.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import AgentSession, CommandError, describe_operations
from .models import SitePaths
from .repository import PubliiRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publii-agent",
        description=(
            "Read and edit a Publii site through a fixed JSON command set. "
            "Reads are immediate, writes go through plan then apply."
        ),
    )
    parser.add_argument("--site", help="Publii site folder, the one containing input/.")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ops", help="Print the command catalogue as JSON and exit.")

    p_read = sub.add_parser("read", help="Run one read command.")
    p_read.add_argument("json", nargs="?", help='e.g. \'{"op":"list_posts"}\'. Omit to read stdin.')

    p_plan = sub.add_parser("plan", help="Describe what a write batch would do.")
    p_plan.add_argument("json", nargs="?", help="A JSON array of commands. Omit to read stdin.")

    p_apply = sub.add_parser("apply", help="Run a write batch in one transaction.")
    p_apply.add_argument("json", nargs="?", help="A JSON array of commands. Omit to read stdin.")

    sub.add_parser("check", help="Run the integrity check and report.")

    return parser


def _payload(raw: str | None) -> object:
    text = raw if raw is not None else sys.stdin.read()
    if not text.strip():
        raise CommandError("No JSON supplied, on the argument or on stdin.")
    return json.loads(text)


def _emit(obj: dict) -> int:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if obj.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(message)s",
        stream=sys.stderr,  # keep stdout pure JSON
    )

    if args.command == "ops":
        return _emit({"ok": True, "result": describe_operations()})

    if not args.site:
        return _emit(
            {"ok": False, "error": {"code": "bad_usage", "detail": "--site is required."}}
        )

    try:
        session = AgentSession(args.site)
    except (FileNotFoundError, OSError) as exc:
        return _emit({"ok": False, "error": {"code": "site_not_found", "detail": str(exc)}})

    try:
        if args.command == "check":
            return _emit(session.read({"op": "check"}))
        if args.command == "read":
            return _emit(session.read(_payload(args.json)))

        batch = _payload(args.json)
        if isinstance(batch, dict):
            batch = [batch]
        if not isinstance(batch, list):
            raise CommandError("Expected a JSON array of commands.")

        if args.command == "plan":
            return _emit(session.plan(batch))
        return _emit(session.apply(batch))

    except CommandError as exc:
        return _emit({"ok": False, "error": {"code": "bad_command", "detail": str(exc)}})
    except json.JSONDecodeError as exc:
        return _emit({"ok": False, "error": {"code": "bad_json", "detail": str(exc)}})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
