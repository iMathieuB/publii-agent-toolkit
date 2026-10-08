"""A JSON command interface, so an AI agent can edit a Publii site safely.

Why this layer exists
---------------------

An agent driving a CMS through a Python API has to be trusted not to write bad
Python. An agent driving it through a fixed set of JSON commands only has to
produce valid JSON, and every command it can express is one the toolkit already
knows how to validate, stage and roll back.

The safety does not come from the agent behaving well. It comes from three
properties of the layer underneath, which hold no matter what the agent asks
for:

1. A plan is separate from its application. ``plan`` describes what would
   happen and touches nothing. Only ``apply`` writes.
2. Every apply runs inside one transaction that takes the write lock first,
   backs up the mutable surface, and restores it on any failure.
3. A structural integrity check runs *before* the commit, not after. A plan that
   would leave a menu pointing at a post that does not exist is rejected and the
   whole transaction rolls back, so the site is never left inconsistent.

The worst outcome of a confused agent is therefore a refused command and a log
line, not a broken site.

Command shape
-------------

A request is a JSON object::

    {"op": "update_post", "slug": "about", "title": "About us"}

A batch is a JSON array of those objects, applied in order, all inside one
transaction. Either every command lands or none does.

Every response is a JSON object with ``ok`` and either ``result`` or ``error``,
so an agent never has to parse prose to find out what happened.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import CoreMeta, MenuItem, Post, PostAdditionalData
from .repository import PubliiIntegrityError, PubliiLockedError, PubliiRepository

log = logging.getLogger(__name__)


class CommandError(ValueError):
    """A command was malformed or referred to something that does not exist."""


@dataclass
class Command:
    """One parsed, validated command."""

    op: str
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "Command":
        if not isinstance(payload, dict):
            raise CommandError(f"Expected an object, got {type(payload).__name__}.")
        op = payload.get("op")
        if not op or not isinstance(op, str):
            raise CommandError('Every command needs a string "op" field.')
        if op not in OPERATIONS:
            raise CommandError(
                f"Unknown op {op!r}. Known ops: {', '.join(sorted(OPERATIONS))}."
            )
        args = {k: v for k, v in payload.items() if k != "op"}
        return cls(op=op, args=args)


# --------------------------------------------------------------------- reading


def _op_list_posts(repo: PubliiRepository, args: dict) -> Any:
    """List posts, pages, or both. args: pages (bool, optional)."""
    pages = args.get("pages")
    posts = repo.list_posts(pages=pages)
    return [
        {
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "status": p.status,
            "is_page": p.is_page,
            "chars": len(p.text or ""),
        }
        for p in posts
    ]


def _op_get_post(repo: PubliiRepository, args: dict) -> Any:
    """Fetch one post with its metadata. args: slug or id."""
    post = _resolve_post(repo, args)
    extra = repo.get_additional_data(post.id)
    return {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "status": post.status,
        "is_page": post.is_page,
        "text": post.text,
        "meta": {
            "metaTitle": extra.core.metaTitle,
            "metaDesc": extra.core.metaDesc,
            "metaRobots": extra.core.metaRobots,
            "canonicalUrl": extra.core.canonicalUrl,
        },
        "tags": repo.get_post_tag_ids(post.id),
    }


def _op_list_tags(repo: PubliiRepository, args: dict) -> Any:
    """List every tag."""
    return [{"id": t.id, "slug": t.slug, "name": t.name} for t in repo.list_tags()]


def _op_list_menus(repo: PubliiRepository, args: dict) -> Any:
    """List menus and their items, nesting preserved."""

    def render(items: list[MenuItem]) -> list[dict]:
        return [
            {
                "id": i.id,
                "label": i.label,
                "type": i.type,
                "link": i.link,
                "items": render(i.items),
            }
            for i in items
        ]

    return [
        {"name": m.name, "position": m.position, "items": render(m.items)}
        for m in repo.read_menus()
    ]


def _op_check(repo: PubliiRepository, args: dict) -> Any:
    """Run the structural integrity check and report, without changing anything."""
    issues = repo.check_integrity()
    return {
        "ok": not any(i.severity == "ERROR" for i in issues),
        "issues": [
            {"code": i.code, "severity": i.severity, "detail": i.detail} for i in issues
        ],
    }


# --------------------------------------------------------------------- writing
# Each writer takes an open transaction. They never open one themselves, which
# is what lets a batch of commands share a single atomic unit of work.


def _op_update_post(repo: PubliiRepository, tx, args: dict) -> Any:
    """Change a post's title, body or status. args: slug|id, title?, text?, status?."""
    post = _resolve_post(repo, args)
    fields = {k: args[k] for k in ("title", "text", "status") if k in args}
    if not fields:
        raise CommandError("update_post needs at least one of: title, text, status.")
    tx.update_post_fields(post.id, **fields)
    return {"id": post.id, "slug": post.slug, "updated": sorted(fields)}


def _op_update_meta(repo: PubliiRepository, tx, args: dict) -> Any:
    """Change SEO metadata. args: slug|id, metaTitle?, metaDesc?, metaRobots?, canonicalUrl?."""
    post = _resolve_post(repo, args)
    current = repo.get_additional_data(post.id)
    known = ("metaTitle", "metaDesc", "metaRobots", "canonicalUrl")
    supplied = {k: args[k] for k in known if k in args}
    if not supplied:
        raise CommandError(f"update_meta needs at least one of: {', '.join(known)}.")

    # Round-trip through the model so keys this toolkit does not know about are
    # preserved rather than silently dropped.
    merged = current.core.model_copy(update=supplied)
    tx.upsert_additional_data(
        post.id,
        PostAdditionalData(
            core=merged,
            postViewSettings=current.postViewSettings,
            pageViewSettings=current.pageViewSettings,
        ),
    )
    return {"id": post.id, "slug": post.slug, "updated": sorted(supplied)}


def _op_set_tags(repo: PubliiRepository, tx, args: dict) -> Any:
    """Replace a post's tags. args: slug|id, tags (list of tag slugs)."""
    post = _resolve_post(repo, args)
    tags = args.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise CommandError('set_tags needs "tags" as a list of tag slugs.')
    tx.set_post_tags(post.id, tags)
    return {"id": post.id, "slug": post.slug, "tags": tags}


def _op_add_menu_item(repo: PubliiRepository, tx, args: dict) -> Any:
    """Add a menu entry. args: position, label, link, type?, parent_id?."""
    for required in ("position", "label", "link"):
        if required not in args:
            raise CommandError(f"add_menu_item needs {required!r}.")

    menus = repo.read_menus()
    used = {i.id for m in menus for i in _flatten(m.items)}
    item = MenuItem(
        id=(max(used) + 1) if used else 1,
        label=args["label"],
        type=args.get("type", "page"),
        link=args["link"],
    )
    tx.inject_menu_item(args["position"], item, parent_id=args.get("parent_id"))
    return {"position": args["position"], "id": item.id, "label": item.label}


def _op_remove_menu_item(repo: PubliiRepository, tx, args: dict) -> Any:
    """Remove a menu entry by id. args: position, id."""
    for required in ("position", "id"):
        if required not in args:
            raise CommandError(f"remove_menu_item needs {required!r}.")
    tx.remove_menu_item(args["position"], int(args["id"]))
    return {"position": args["position"], "removed": args["id"]}


def _op_update_menu_item(repo: PubliiRepository, tx, args: dict) -> Any:
    """Rename a menu entry in place, keeping its position. args: position, id, label?, title?."""
    _check_menu_update(args)
    fields = {k: args[k] for k in MENU_UPDATE_FIELDS if k in args}
    tx.update_menu_item(args["position"], int(args["id"]), **fields)
    return {"position": args["position"], "id": args["id"], **fields}


# Fields an agent may change on an existing menu entry. Its target (type, link)
# is left out on purpose: pointing an entry elsewhere is a remove and an add.
MENU_UPDATE_FIELDS = ("label", "title")


def _check_menu_update(args: dict) -> None:
    for required in ("position", "id"):
        if required not in args:
            raise CommandError(f"update_menu_item needs {required!r}.")
    if not any(k in args for k in MENU_UPDATE_FIELDS):
        raise CommandError(f"update_menu_item needs at least one of {list(MENU_UPDATE_FIELDS)}.")
    extra = sorted(set(args) - {"position", "id", *MENU_UPDATE_FIELDS})
    if extra:
        raise CommandError(f"update_menu_item cannot change {extra}.")


READ_OPS: dict[str, Callable] = {
    "list_posts": _op_list_posts,
    "get_post": _op_get_post,
    "list_tags": _op_list_tags,
    "list_menus": _op_list_menus,
    "check": _op_check,
}

WRITE_OPS: dict[str, Callable] = {
    "update_post": _op_update_post,
    "update_meta": _op_update_meta,
    "set_tags": _op_set_tags,
    "add_menu_item": _op_add_menu_item,
    "remove_menu_item": _op_remove_menu_item,
    "update_menu_item": _op_update_menu_item,
}

OPERATIONS = {**READ_OPS, **WRITE_OPS}


# ----------------------------------------------------------------- the session


class AgentSession:
    """Runs commands against one site.

    Read commands work straight away. Write commands are described by ``plan``
    and only performed by ``apply``.
    """

    def __init__(self, site_root: str, *, logger: logging.Logger | None = None) -> None:
        self.repo = PubliiRepository.open(site_root)
        self.log = logger or log

    # -- reads ------------------------------------------------------------- #

    def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = Command.parse(payload)
        if cmd.op not in READ_OPS:
            raise CommandError(
                f"{cmd.op!r} writes. Use plan() to preview it and apply() to run it."
            )
        return _ok(READ_OPS[cmd.op](self.repo, cmd.args))

    # -- writes ------------------------------------------------------------ #

    def plan(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Describe what a batch would do. Opens no transaction, writes nothing.

        Everything checkable by reading is checked here, so an agent learns that
        a slug does not exist before anything is locked or backed up.

        A problem in one step marks the whole plan not-ok, because ``apply`` is
        all-or-nothing: a batch containing one bad command lands none of them.
        Every step is still described, so the agent can fix all the problems in
        one pass instead of discovering them one at a time.
        """
        steps: list[dict[str, Any]] = []
        problems: list[str] = []

        for index, payload in enumerate(payloads):
            cmd = Command.parse(payload)
            if cmd.op in READ_OPS:
                raise CommandError(f"{cmd.op!r} is a read. Use read() for it.")

            step: dict[str, Any] = {"index": index, "op": cmd.op, "args": cmd.args}
            try:
                step["describes"] = _describe_or_raise(self.repo, cmd)
            except CommandError as exc:
                step["problem"] = str(exc)
                step["describes"] = f"would fail: {exc}"
                problems.append(f"step {index} ({cmd.op}): {exc}")
            steps.append(step)

        result = {"steps": steps, "count": len(steps), "problems": problems}
        if problems:
            return {
                "ok": False,
                "error": {
                    "code": "plan_invalid",
                    "detail": (
                        f"{len(problems)} of {len(steps)} step(s) cannot run. "
                        f"apply() is all-or-nothing, so none would land."
                    ),
                },
                "result": result,
            }
        return _ok(result)

    def apply(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Run a batch inside one transaction.

        Either every command lands or none does. The integrity check runs before
        the commit, so a batch that would corrupt the site is refused whole.
        """
        commands = [Command.parse(p) for p in payloads]
        for cmd in commands:
            if cmd.op in READ_OPS:
                raise CommandError(f"{cmd.op!r} is a read. Use read() for it.")

        results = []
        try:
            with self.repo.transaction() as tx:
                for cmd in commands:
                    results.append(
                        {"op": cmd.op, "result": WRITE_OPS[cmd.op](self.repo, tx, cmd.args)}
                    )
        except PubliiLockedError as exc:
            return _err("site_locked", str(exc))
        except PubliiIntegrityError as exc:
            return _err("integrity_failed", str(exc))
        except CommandError as exc:
            return _err("bad_command", str(exc))

        return _ok({"applied": results, "count": len(results)})


# --------------------------------------------------------------------- helpers


def _resolve_post(repo: PubliiRepository, args: dict) -> Post:
    if "slug" in args:
        post = repo.get_post_by_slug(str(args["slug"]))
        if post is None:
            raise CommandError(f"No post or page with slug {args['slug']!r}.")
        return post
    if "id" in args:
        post = repo.get_post(int(args["id"]))
        if post is None:
            raise CommandError(f"No post or page with id {args['id']}.")
        return post
    raise CommandError('Identify the post with "slug" or "id".')


def _describe_or_raise(repo: PubliiRepository, cmd: Command) -> str:
    """A one-line summary of what a command would do.

    Raises CommandError when the command cannot run at all, which is how
    ``plan`` finds problems without opening a transaction.
    """
    if cmd.op in {"update_post", "update_meta", "set_tags"}:
        post = _resolve_post(repo, cmd.args)
        fields = sorted(k for k in cmd.args if k not in {"slug", "id"})
        if not fields:
            raise CommandError(f"{cmd.op} needs at least one field to change.")
        return f"{cmd.op} on {post.slug!r} (id {post.id}): {', '.join(fields)}"

    if cmd.op == "add_menu_item":
        for required in ("position", "label", "link"):
            if required not in cmd.args:
                raise CommandError(f"add_menu_item needs {required!r}.")
        return f"add {cmd.args['label']!r} to menu {cmd.args['position']!r}"

    if cmd.op == "remove_menu_item":
        for required in ("position", "id"):
            if required not in cmd.args:
                raise CommandError(f"remove_menu_item needs {required!r}.")
        return f"remove item {cmd.args['id']} from menu {cmd.args['position']!r}"

    if cmd.op == "update_menu_item":
        _check_menu_update(cmd.args)
        menu = next((m for m in repo.read_menus() if m.position == cmd.args["position"]), None)
        if menu is None:
            raise CommandError(f"No menu at position {cmd.args['position']!r}.")
        item = next((i for i in _flatten(menu.items) if i.id == int(cmd.args["id"])), None)
        if item is None:
            raise CommandError(f"No item {cmd.args['id']} in menu {cmd.args['position']!r}.")
        changes = ", ".join(
            f"{k} {getattr(item, k)!r} -> {cmd.args[k]!r}" for k in MENU_UPDATE_FIELDS if k in cmd.args
        )
        return f"update item {cmd.args['id']} in menu {cmd.args['position']!r}: {changes}"

    return cmd.op


def _flatten(items: list[MenuItem]) -> list[MenuItem]:
    out: list[MenuItem] = []
    for item in items:
        out.append(item)
        out.extend(_flatten(item.items))
    return out


def _ok(result: Any) -> dict[str, Any]:
    return {"ok": True, "result": result}


def _err(code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "detail": detail}}


def describe_operations() -> dict[str, Any]:
    """Machine-readable catalogue of every command, for an agent to read once.

    Returning this rather than expecting the agent to have memorised the API is
    what keeps the contract stable as the toolkit grows.
    """
    return {
        "read": {name: (fn.__doc__ or "").strip() for name, fn in sorted(READ_OPS.items())},
        "write": {name: (fn.__doc__ or "").strip() for name, fn in sorted(WRITE_OPS.items())},
    }
