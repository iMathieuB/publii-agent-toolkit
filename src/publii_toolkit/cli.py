"""Command line interface, shaped for an agent as much as for a person.

Every subcommand reads JSON on stdin or from an argument and writes one JSON
object on stdout. Exit status is 0 when ``ok`` is true and 1 when it is not, so
a caller can branch on either.
"""

from __future__ import annotations

import argparse
import json
import locale
import logging
import sys
from pathlib import Path

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
    p_read.add_argument("json", nargs="?", help='e.g. \'{"op":"list_posts"}\', or @file.json (UTF-8). Omit to read stdin.')

    p_plan = sub.add_parser("plan", help="Describe what a write batch would do.")
    p_plan.add_argument("json", nargs="?", help="A JSON array of commands, or @file.json (UTF-8). Omit to read stdin.")

    p_apply = sub.add_parser("apply", help="Run a write batch in one transaction.")
    p_apply.add_argument("json", nargs="?", help="A JSON array of commands, or @file.json (UTF-8). Omit to read stdin.")

    sub.add_parser("check", help="Run the integrity check and report.")

    p_status = sub.add_parser(
        "desktop-status", help="List local Publii sites and when each was last synced."
    )
    p_status.add_argument("--sites-root", help="Folder holding the Publii site directories.")

    p_sync = sub.add_parser(
        "desktop-sync",
        help="Render and publish sites by driving the Publii desktop app (Windows only).",
    )
    p_sync.add_argument("--sites-root", help="Folder holding the Publii site directories.")
    p_sync.add_argument("--name", action="append", default=[], required=True,
                        help="Site directory name; repeat for several, published in order.")
    p_sync.add_argument("--apply", action="store_true",
                        help="Actually publish. Without it this is a dry run.")
    p_sync.add_argument("--keep-open", action="store_true",
                        help="Leave Publii running afterwards; it then blocks database writes.")

    return parser


def _desktop(args) -> int:
    """The desktop driver is optional and Windows only; import it lazily."""
    try:
        from .desktop import DesktopError, PubliiDesktop
    except ImportError as exc:  # pragma: no cover - depends on the extra
        return _emit({"ok": False, "error": {
            "code": "desktop_unavailable",
            "detail": f'Install the extra: pip install "publii-agent-toolkit[desktop]" ({exc})',
        }})

    try:
        driver = PubliiDesktop(sites_root=args.sites_root)
        if args.command == "desktop-status":
            return _emit({"ok": True, "result": {
                "publii_running": driver.is_running(),
                "sites": [
                    {"directory": s.directory, "display_name": s.display_name,
                     "last_sync": s.last_sync}
                    for s in driver.sites()
                ],
            }})

        results = driver.sync(args.name, apply=args.apply, keep_open=args.keep_open)
        return _emit({
            "ok": all(r.succeeded for r in results) if args.apply else True,
            "result": {
                "applied": args.apply,
                "sites": [
                    {"site": r.site, "succeeded": r.succeeded,
                     "previous_sync_ms": r.previous_sync_ms,
                     "new_sync_ms": r.new_sync_ms, "detail": r.detail}
                    for r in results
                ],
            },
        })
    except DesktopError as exc:
        return _emit({"ok": False, "error": {"code": "desktop_error", "detail": str(exc)}})


def _decode(data: bytes) -> str:
    """Bytes to text, UTF-8 first. sys.stdin.read() would use the Windows code
    page (cp1252) and turn the "é" an agent sends as UTF-8 into "Ã©"."""
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False))


def _payload(raw: str | None) -> object:
    if raw is not None and raw.startswith("@"):
        text = _decode(Path(raw[1:]).expanduser().read_bytes())
    else:
        buffer = getattr(sys.stdin, "buffer", None)  # absent when stdin is replaced by a StringIO
        text = raw if raw is not None else (_decode(buffer.read()) if buffer else sys.stdin.read())
    if not text.strip():
        raise CommandError("No JSON supplied, on the argument or on stdin.")
    return json.loads(text)


def _emit(obj: dict) -> int:
    # ASCII with \u escapes: the same bytes in every console code page, and any
    # JSON reader turns them back into the accented text.
    json.dump(obj, sys.stdout, ensure_ascii=True, indent=2)
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

    if args.command in ("desktop-status", "desktop-sync"):
        return _desktop(args)

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
