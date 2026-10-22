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
transaction. Either every command lands or none does. A later command may refer
to a page an earlier command of the same batch creates, by its slug: a site can
be built, menu and settings included, in one plan and one apply.

Every response is a JSON object with ``ok`` and either ``result`` or ``error``,
so an agent never has to parse prose to find out what happened.
"""

from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import images
from .models import CoreMeta, Menu, MenuItem, Post, PostAdditionalData
from .repository import (
    CUSTOM_CSS,
    SITE_CONFIG,
    THEME_CONFIG,
    PubliiIntegrityError,
    PubliiLockedError,
    PubliiRepository,
)

log = logging.getLogger(__name__)


class CommandError(ValueError):
    """A command was malformed or referred to something that does not exist."""


def _mojibake(value: Any) -> str | None:
    """The first word that reads as UTF-8 text decoded as Windows-1252, if any.

    "Débutants" sent as UTF-8 and read with Windows' default code page arrives
    as "DÃ©butants". Such a word encodes back to cp1252 bytes that form valid
    UTF-8; a genuine accented word does not ("é" is the lone byte 0xE9, never
    valid UTF-8 on its own), so the test has no false alarm on real French,
    Portuguese or symbols like € and →.
    """
    if isinstance(value, str):
        for word in value.split():
            if word.isascii():
                continue
            try:
                fixed = word.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if fixed != word:
                return word
        return None
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = _mojibake(item)
            if found:
                return found
    return None


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
        garbled = _mojibake(args)
        if garbled:
            raise CommandError(
                f"{op}: {garbled!r} looks like UTF-8 text read as Windows-1252 (for example "
                f"'Ã©' instead of 'é'). Nothing was written. Send the batch as UTF-8: as the "
                f"argument, as @file.json, or on stdin with publii-agent 1.3.5 or later."
            )
        return cls(op=op, args=args)


# ------------------------------------------------------------ verified facts
# Everything below was read from Publii 0.47.6 (resources/app.asar) or from
# live sites, not assumed. The source file is named where it matters.

# Editors this toolkit writes page text for. Publii offers three when a page is
# created (src/components/Pages.vue): "tinymce" (WYSIWYG, stores HTML),
# "markdown" (stores Markdown) and "blockeditor" (stores a JSON block structure,
# which this toolkit does not generate). There is no site-wide default: the
# person picks one per page, and a brand-new site has no page to infer it from.
WRITABLE_EDITORS = ("markdown", "tinymce")

PAGE_STATUSES = ("published", "draft")

# Publii slugs are lower-case ASCII words joined by hyphens (back-end/helpers/
# slug.js). Anything else would be rewritten by Publii on the next save.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Images Publii itself accepts (back-end/image.js), plus SVG and GIF, which it
# copies without resizing. Checked against the file's first bytes, not its name.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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
            "editor": extra.core.editor,
        },
        "tags": repo.get_post_tag_ids(post.id),
    }


def _op_list_tags(repo: PubliiRepository, args: dict) -> Any:
    """List every tag."""
    return [{"id": t.id, "slug": t.slug, "name": t.name} for t in repo.list_tags()]


def _op_list_menus(repo: PubliiRepository, args: dict) -> Any:
    """List menus and their items, nesting preserved. A brand-new site has none."""

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


def _deployment_summary(site: dict) -> dict:
    """Where the server settings point. Never a credential: for S3, keys_set only."""
    deployment = site.get("deployment") or {}
    out: dict[str, Any] = {"protocol": deployment.get("protocol") or "",
                           "website_url": site.get("domain") or ""}
    if out["protocol"] == "s3":
        s3 = deployment.get("s3") or {}
        out["endpoint"] = s3.get("endpoint") or ""
        out["bucket"] = s3.get("bucket") or ""
        out["region"] = (s3.get("customRegion") if s3.get("customProvider") else s3.get("region")) or ""
        out["keys_set"] = bool(s3.get("id")) and bool(s3.get("key"))
    return out


def _op_get_site_settings(repo: PubliiRepository, args: dict) -> Any:
    """The settings update_site_settings can change, where the server settings point (set_s3_deployment), the theme's menu positions, and the editors in use. Never returns credentials: deployment.keys_set only says whether both S3 keys are filled."""
    view = _PlanView(repo)
    site = repo.read_site_config()
    theme = repo.read_theme_config()
    advanced = site.get("advanced") or {}
    gdpr = advanced.get("gdpr") or {}
    return {
        # Built field by field from the whitelist: nothing else from
        # site.config.json, which also holds the deployment credentials, can
        # reach the output.
        "site_name": site.get("displayName", ""),
        "site_description": advanced.get("metaDescription", ""),
        "logo": ((theme.get("config") or {}).get("logo") or ""),
        "cookie_banner": bool(gdpr.get("enabled", False)),
        "privacy_policy_page": gdpr.get("privacyPolicyPostId") or None,
        "front_page": advanced.get("pageAsFrontpage") if advanced.get("usePageAsFrontpage") else None,
        "theme": site.get("theme", ""),
        "deployment": _deployment_summary(site),
        "theme_menu_positions": {
            position: {"name": spec.get("name", position), "in_use": position in view.menu_positions()}
            for position, spec in _theme_menus(view).items()
        },
        "content_editors": repo.content_editors(),
    }


def _op_check(repo: PubliiRepository, args: dict) -> Any:
    """Run the structural integrity check and report, without changing anything."""
    issues = repo.check_integrity()
    return {
        "ok": not any(i.severity == "ERROR" for i in issues),
        "issues": [
            {"code": i.code, "severity": i.severity, "detail": i.detail} for i in issues
        ],
    }


# ------------------------------------------------------------- two site views
# A write command is validated twice: by plan, against the site as it is on
# disk plus whatever earlier steps of the batch would create, and by apply,
# against the open transaction. Both views answer the same questions, so each
# command has a single validation function and plan cannot drift from apply.


@dataclass
class PostRef:
    id: int | None  # None while the page only exists later in the batch being planned
    slug: str
    is_page: bool


class _PlanView:
    """The site on disk, plus what earlier steps of the planned batch would add."""

    def __init__(self, repo: PubliiRepository) -> None:
        self.repo = repo
        self.input_dir = repo.paths.input_dir
        self.media_dir = repo.paths.media_dir
        self.pending_pages: dict[str, str] = {}  # slug -> editor
        self.pending_menus: set[str] = set()
        self.pending_media: set[Path] = set()

    def post(self, args: dict) -> PostRef:
        if "slug" in args and str(args["slug"]) in self.pending_pages:
            return PostRef(id=None, slug=str(args["slug"]), is_page=True)
        post = _resolve_post(self.repo, args)
        return PostRef(id=post.id, slug=post.slug, is_page=post.is_page)

    def slug_taken(self, slug: str) -> bool:
        return slug in self.pending_pages or self.repo.get_post_by_slug(slug) is not None

    def editors(self) -> dict[str, int]:
        counts = dict(self.repo.content_editors())
        for editor in self.pending_pages.values():
            counts[editor] = counts.get(editor, 0) + 1
        return counts

    def menus(self) -> list[Menu]:
        return self.repo.read_menus()

    def menu_positions(self) -> set[str]:
        return _positions(self.menus()) | self.pending_menus

    def site_config(self) -> dict:
        return self.repo.read_site_config()

    def theme_config(self) -> dict:
        return self.repo.read_theme_config()

    def custom_css(self) -> str:
        path = self.input_dir / "config" / CUSTOM_CSS
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def media_present(self, path: Path) -> bool:
        return path.resolve() in self.pending_media or path.is_file()

    def media_staged(self, path: Path) -> bool:
        return path.resolve() in self.pending_media

    def has_author(self) -> bool:
        return self.repo.first_author_id() is not None


class _ApplyView:
    """The open transaction: committed rows plus everything staged so far."""

    def __init__(self, repo: PubliiRepository, tx) -> None:
        self.repo = repo
        self.tx = tx
        self.input_dir = repo.paths.input_dir
        self.media_dir = repo.paths.media_dir

    def post(self, args: dict) -> PostRef:
        post = _resolve_post_tx(self.tx, args)
        return PostRef(id=post.id, slug=post.slug, is_page=post.is_page)

    def slug_taken(self, slug: str) -> bool:
        return self.tx.find_post(slug=slug) is not None

    def editors(self) -> dict[str, int]:
        return self.tx.content_editors()

    def menus(self) -> list[Menu]:
        return self.tx.current_menus()

    def menu_positions(self) -> set[str]:
        return _positions(self.menus())

    def site_config(self) -> dict:
        return self.tx.current_site_config()

    def theme_config(self) -> dict:
        return self.tx.current_theme_config()

    def custom_css(self) -> str:
        return self.tx.current_custom_css()

    def media_present(self, path: Path) -> bool:
        return path.resolve() in self.tx.staged_media() or path.is_file()

    def media_staged(self, path: Path) -> bool:
        return path.resolve() in self.tx.staged_media()

    def has_author(self) -> bool:
        return self.tx.first_author_id() is not None


def _positions(menus: list[Menu]) -> set[str]:
    # Publii stores a menu shown in several places as "mainMenu;footerMenu".
    return {p for m in menus for p in (m.position or "").split(";") if p}


# --------------------------------------------------------------------- writing
# Each writer takes an open transaction. They never open one themselves, which
# is what lets a batch of commands share a single atomic unit of work.


def _op_update_post(repo: PubliiRepository, tx, args: dict) -> Any:
    """Change a post's title, body or status. args: slug|id, title?, text?, status?."""
    post = _resolve_post_tx(tx, args)
    fields = {k: args[k] for k in ("title", "text", "status") if k in args}
    if not fields:
        raise CommandError("update_post needs at least one of: title, text, status.")
    tx.update_post_fields(post.id, **fields)
    return {"id": post.id, "slug": post.slug, "updated": sorted(fields)}


def _op_update_meta(repo: PubliiRepository, tx, args: dict) -> Any:
    """Change SEO metadata. args: slug|id, metaTitle?, metaDesc?, metaRobots?, canonicalUrl?."""
    post = _resolve_post_tx(tx, args)
    # Read through the transaction: a page created earlier in the batch has a
    # _core row only there, and rebuilding it from defaults would reset its editor.
    current = tx.get_additional_data(post.id)
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
    post = _resolve_post_tx(tx, args)
    tags = args.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise CommandError('set_tags needs "tags" as a list of tag slugs.')
    tx.set_post_tags(post.id, tags)
    return {"id": post.id, "slug": post.slug, "tags": tags}


# -- create_page -------------------------------------------------------------- #

CREATE_PAGE_ARGS = {"title", "slug", "text", "editor", "status", "metaTitle", "metaDesc",
                    "parent_id", "parent"}


def _check_create_page(view, args: dict) -> dict:
    extra = sorted(set(args) - CREATE_PAGE_ARGS)
    if extra:
        raise CommandError(f"create_page does not take {extra}. Allowed: {sorted(CREATE_PAGE_ARGS)}.")
    for required in ("title", "slug", "text"):
        if not isinstance(args.get(required), str):
            raise CommandError(f"create_page needs {required!r} as a string.")
    title, slug, text = args["title"].strip(), args["slug"], args["text"]
    if not title:
        raise CommandError("create_page needs a non-empty title.")
    if not SLUG_RE.match(slug):
        raise CommandError(
            f"Slug {slug!r} is not one Publii would keep: use lower-case letters, digits "
            f"and single hyphens, e.g. 'a-propos'."
        )
    restricted = _restricted_slugs(view.site_config())
    if slug in restricted:
        raise CommandError(f"Slug {slug!r} is reserved by Publii's URL scheme ({sorted(restricted)}).")
    if view.slug_taken(slug):
        raise CommandError(f"Slug {slug!r} is already used on this site. Slugs must be unique.")

    status = args.get("status", "published")
    if status not in PAGE_STATUSES:
        raise CommandError(f"status must be one of {list(PAGE_STATUSES)}, not {status!r}.")

    editor = _choose_editor(view, args.get("editor"))
    if editor == "tinymce" and text.strip() and not text.lstrip().startswith("<"):
        raise CommandError(
            "This page uses the WYSIWYG editor (tinymce), which stores HTML. Wrap the text "
            "in tags, e.g. '<p>Bienvenue</p>', or create the page with editor 'markdown'."
        )

    for key in ("metaTitle", "metaDesc"):
        if key in args and not isinstance(args[key], str):
            raise CommandError(f"{key} must be a string.")

    if "parent_id" in args and "parent" in args:
        raise CommandError("Give the parent page as parent_id or as parent (its slug), not both.")
    parent: PostRef | None = None
    if "parent_id" in args or "parent" in args:
        ref_args = {"id": args["parent_id"]} if "parent_id" in args else {"slug": args["parent"]}
        parent = view.post(ref_args)
        if not parent.is_page:
            raise CommandError(f"Parent {parent.slug!r} is a post, not a page.")

    if not view.has_author():
        raise CommandError("The site has no author row; open it once in Publii first.")

    return {"title": title, "slug": slug, "text": text, "status": status, "editor": editor,
            "metaTitle": args.get("metaTitle", ""), "metaDesc": args.get("metaDesc", ""),
            "parent": parent}


def _choose_editor(view, requested: Any) -> str:
    """The editor a new page is stored for: as asked, or the one the site uses.

    Inferred only when every existing post and page uses the same editor.
    Ordinata's own sites mix Markdown and WYSIWYG pages, and a brand-new site
    has no page at all, so both cases need the editor spelled out.
    """
    if requested is not None:
        if requested not in WRITABLE_EDITORS:
            raise CommandError(
                f"editor must be one of {list(WRITABLE_EDITORS)}, not {requested!r}. "
                f"Publii's block editor stores a JSON structure this toolkit does not write."
            )
        return requested
    counts = view.editors()
    if len(counts) == 1:
        (only,) = counts
        if only in WRITABLE_EDITORS:
            return only
        raise CommandError(
            f"Every page on this site uses the {only!r} editor, which this toolkit does not "
            f"write. Pass editor 'markdown' or 'tinymce' explicitly."
        )
    found = ", ".join(f"{name} x{n}" for name, n in sorted(counts.items())) or "no page yet"
    raise CommandError(
        f"Cannot tell which editor this page should use ({found}). Pass \"editor\": "
        f"\"tinymce\" (Publii's WYSIWYG editor, text in HTML, easiest for the owner to edit "
        f"later) or \"markdown\" (text in Markdown)."
    )


def _restricted_slugs(site_config: dict) -> set[str]:
    # Publii refuses these for pages when clean URLs are on (back-end/page.js,
    # checkAndPrepareSlug) and renames the page. Refused here whatever the URL
    # mode, plus the pagination folder name, so a page never collides with them.
    urls = ((site_config.get("advanced") or {}).get("urls") or {})
    names = {"assets", "media",
             urls.get("authorsPrefix", "authors"),
             urls.get("tagsPrefix", "tags"),
             urls.get("postsPrefix", ""),
             urls.get("pageName", "page")}
    return {n for n in names if n}


def _op_create_page(repo: PubliiRepository, tx, args: dict) -> Any:
    """Create a page and register it in pages.config.json. args: title, slug (unique), text, editor? ('tinymce' = HTML, 'markdown'; required when the site does not settle it), status? (published|draft), metaTitle?, metaDesc?, parent_id? or parent? (slug of the parent page)."""
    spec = _check_create_page(_ApplyView(repo, tx), args)
    author = tx.first_author_id()
    post = Post(
        id=0,
        title=spec["title"],
        authors=str(author),
        slug=spec["slug"],
        text=spec["text"],
        created_at=0,
        modified_at=0,
        status=f"{spec['status']},is-page",
        template="",
    )
    # The _core blob Publii writes for a page has exactly these five keys
    # (verified on live sites); CoreMeta dumps them and nothing else.
    core = CoreMeta(metaTitle=spec["metaTitle"], metaDesc=spec["metaDesc"], editor=spec["editor"])
    parent: PostRef | None = spec["parent"]
    new_id = tx.create_post(post, core=core, register_as_page=parent is None)
    if parent is not None:
        tx.register_page(new_id, parent_id=parent.id)
    return {"id": new_id, "slug": spec["slug"], "editor": spec["editor"],
            "status": post.status, "parent_id": parent.id if parent else None}


# -- menus -------------------------------------------------------------------- #


def _theme_manifest(view) -> dict:
    """The active theme's own config.json (menus, responsive image sizes), or {}."""
    theme = str(view.site_config().get("theme") or "")
    if not theme or not MEDIA_NAME_RE.match(theme):
        return {}
    path = view.input_dir / "themes" / theme / "config.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _theme_menus(view) -> dict[str, dict]:
    """Menu positions the active theme renders, from its own config.json."""
    menus = _theme_manifest(view).get("menus") or {}
    return menus if isinstance(menus, dict) else {}


def _check_create_menu(view, args: dict) -> dict:
    extra = sorted(set(args) - {"position", "name"})
    if extra:
        raise CommandError(f"create_menu does not take {extra}.")
    position = args.get("position")
    if not isinstance(position, str) or not position:
        raise CommandError("create_menu needs 'position', one of the theme's menu positions.")
    available = _theme_menus(view)
    if position not in available:
        raise CommandError(
            f"The theme has no menu position {position!r}. Available: {sorted(available) or 'none found'}."
        )
    if position in view.menu_positions():
        raise CommandError(f"A menu already uses position {position!r}; add items to it instead.")
    name = args.get("name", available[position].get("name") or position)
    if not isinstance(name, str) or not name.strip():
        raise CommandError("create_menu 'name' must be a non-empty string.")
    return {"position": position, "name": name.strip(),
            "max_levels": available[position].get("maxLevels", -1)}


def _op_create_menu(repo: PubliiRepository, tx, args: dict) -> Any:
    """Create an empty menu at one of the theme's positions (a new site has no menu at all). args: position (e.g. mainMenu, footerMenu; see get_site_settings), name?."""
    spec = _check_create_menu(_ApplyView(repo, tx), args)
    tx.create_menu(spec["name"], spec["position"], max_levels=spec["max_levels"])
    return {"position": spec["position"], "name": spec["name"]}


def _check_add_menu_item(view, args: dict) -> dict:
    for required in ("position", "label"):
        if required not in args:
            raise CommandError(f"add_menu_item needs {required!r}.")
    if ("link" in args) == ("page" in args):
        raise CommandError("add_menu_item needs exactly one of 'link' (a post id, a URL or '#') "
                           "or 'page' (a page slug).")
    if args["position"] not in view.menu_positions():
        raise CommandError(
            f"No menu at position {args['position']!r}. Create it first with create_menu."
        )
    if "parent_id" in args:
        parent_id = _as_int(args["parent_id"], "parent_id")
        items = [i for m in view.menus() if args["position"] in (m.position or "").split(";")
                 for i in _flatten(m.items)]
        if not any(i.id == parent_id for i in items):
            raise CommandError(f"No item {parent_id} in menu {args['position']!r}.")
    if "page" in args:
        ref = view.post({"slug": args["page"]})
        return {"type": args.get("type", "page" if ref.is_page else "post"), "ref": ref}
    return {"type": args.get("type", "page"), "ref": None}


def _op_add_menu_item(repo: PubliiRepository, tx, args: dict) -> Any:
    """Add a menu entry. args: position, label, and link (post id, URL or '#') or page (a page slug, even one created earlier in the batch); type?, parent_id?."""
    spec = _check_add_menu_item(_ApplyView(repo, tx), args)
    link = spec["ref"].id if spec["ref"] is not None else args["link"]
    # Ids come from the menus as staged in this transaction: two entries added
    # in one batch used to get the same id, both computed from the file on disk.
    used = {i.id for m in tx.current_menus() for i in _flatten(m.items)}
    item = MenuItem(
        id=(max(used) + 1) if used else 1,
        label=args["label"],
        type=spec["type"],
        link=link,
    )
    tx.inject_menu_item(args["position"], item, parent_id=args.get("parent_id"))
    return {"position": args["position"], "id": item.id, "label": item.label, "link": link}


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


# -- update_site_settings ----------------------------------------------------- #
# A strict whitelist. site.config.json also holds the deployment credentials
# (S3 keys, FTP passwords, tokens) and dozens of rendering switches; none of
# that is reachable from here, and nothing read from it is ever echoed back
# except the whitelisted values themselves.
#
# Where each value lives was read from live sites and from Publii 0.47.6:
#   site_name         site.config.json  displayName (the %sitename in titles)
#   site_description  site.config.json  advanced.metaDescription (the default
#                                       meta description, home.js / page.js)
#   logo              theme.config.json config.logo, "media/website/<file>"
#                     (site.config.json "logo" is only the icon in Publii's
#                     own site list, not the logo on the website)
#   cookie_banner     site.config.json  advanced.gdpr.enabled
#   privacy_policy_page  advanced.gdpr.privacyPolicyPostId (an integer id),
#                     with privacyPolicyLinkType "internal" and
#                     showPrivacyPolicyLink true (render-html/helpers/gdpr.js)
#   front_page        advanced.usePageAsFrontpage true and
#                     advanced.pageAsFrontpage (an integer id)
#
# A write under advanced.gdpr also sets advanced.gdpr.settingsVersion to "v2"
# when it is empty, as Publii's own "Save settings" does. Publii 0.40 and later
# read an enabled banner without that marker as a pre-0.40 configuration and
# open a "review your Privacy Settings" dialog each time the site loads; the
# dialog covers the window, so desktop-sync cannot reach the sync button. Seen
# on Publii 0.48.1 on 2026-10-20; 0.47.6 holds the same test.

SITE_SETTINGS = ("site_name", "site_description", "logo", "cookie_banner",
                 "privacy_policy_page", "front_page")
GDPR_SETTINGS_VERSION = "v2"


def _check_update_site_settings(view, args: dict) -> dict:
    refused = sorted(set(args) - set(SITE_SETTINGS))
    if refused:
        raise CommandError(
            f"update_site_settings cannot change {refused}. Allowed: {list(SITE_SETTINGS)}. "
            f"Anything else, deployment credentials included, is set in Publii itself."
        )
    if not args:
        raise CommandError(f"update_site_settings needs at least one of {list(SITE_SETTINGS)}.")

    site: dict[tuple[str, ...], Any] = {}
    theme: dict[tuple[str, ...], Any] = {}
    shown: dict[str, Any] = {}

    if "site_name" in args:
        name = args["site_name"]
        if not isinstance(name, str) or not name.strip():
            raise CommandError("site_name must be a non-empty string.")
        site[("displayName",)] = shown["site_name"] = name.strip()

    if "site_description" in args:
        desc = args["site_description"]
        if not isinstance(desc, str):
            raise CommandError("site_description must be a string.")
        site[("advanced", "metaDescription")] = shown["site_description"] = desc.strip()

    if "logo" in args:
        logo = args["logo"]
        if not isinstance(logo, str):
            raise CommandError("logo must be a string: the value add_media returned, or '' to remove it.")
        if logo:
            prefix = "media/website/"
            name = logo[len(prefix):] if logo.startswith(prefix) else ""
            if not name or not MEDIA_NAME_RE.match(name) or ".." in name:
                raise CommandError(
                    f"logo must look like 'media/website/<file>', the value add_media returns "
                    f"for target 'logo'; got {logo!r}."
                )
            if not view.media_present(view.input_dir / "media" / "website" / name):
                raise CommandError(f"No file {logo!r} on the site. Add it first with add_media.")
        theme[("config", "logo")] = shown["logo"] = logo

    if "cookie_banner" in args:
        if not isinstance(args["cookie_banner"], bool):
            raise CommandError("cookie_banner must be true or false.")
        site[("advanced", "gdpr", "enabled")] = shown["cookie_banner"] = args["cookie_banner"]

    refs: dict[str, PostRef] = {}
    for key in ("privacy_policy_page", "front_page"):
        if key not in args:
            continue
        value = args[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise CommandError(f"{key} must be a page slug or a page id.")
        ref = view.post({"id": value} if isinstance(value, int) else {"slug": value})
        if not ref.is_page:
            raise CommandError(f"{key}: {ref.slug!r} is a post, not a page.")
        refs[key] = ref
        shown[key] = ref.slug

    return {"site": site, "theme": theme, "refs": refs, "shown": shown}


def _op_update_site_settings(repo: PubliiRepository, tx, args: dict) -> Any:
    """Change whitelisted site settings only. args, one or more of: site_name, site_description (default meta description), logo (value returned by add_media target 'logo', or ''), cookie_banner (true/false, Publii's GDPR banner), privacy_policy_page (page slug or id the banner links to), front_page (page slug or id served at the site root). Any other key is refused."""
    view = _ApplyView(repo, tx)
    spec = _check_update_site_settings(view, args)
    site = dict(spec["site"])
    refs = spec["refs"]
    if "privacy_policy_page" in refs:
        site[("advanced", "gdpr", "privacyPolicyPostId")] = refs["privacy_policy_page"].id
        site[("advanced", "gdpr", "privacyPolicyLinkType")] = "internal"
        site[("advanced", "gdpr", "showPrivacyPolicyLink")] = True
    if any(path[:2] == ("advanced", "gdpr") for path in site):
        gdpr = (view.site_config().get("advanced") or {}).get("gdpr") or {}
        if not gdpr.get("settingsVersion"):
            site[("advanced", "gdpr", "settingsVersion")] = GDPR_SETTINGS_VERSION
    if "front_page" in refs:
        site[("advanced", "usePageAsFrontpage")] = True
        site[("advanced", "pageAsFrontpage")] = refs["front_page"].id
    if site:
        tx.set_config_values(SITE_CONFIG, site)
    if spec["theme"]:
        tx.set_config_values(THEME_CONFIG, spec["theme"])
    return {"updated": spec["shown"]}


# -- set_s3_deployment -------------------------------------------------------- #
# Fills Publii's "S3 compatible storage" server settings, all but the two keys.
# The site owner pastes the Access ID and the Secret key in Publii, where they
# belong: this command refuses them, and nothing here reads them back (the
# summary in get_site_settings only says whether both are filled).
#
# Paths and values are the ones Publii writes itself for Cloudflare R2, read on
# 2026-10-21 from a site saved in Publii 0.48.1 and one saved in 0.47.6:
#   domain                        "https://<host>"   (Server, Website URL)
#   deployment.protocol           "s3"
#   deployment.s3.customProvider  true               ("Use a custom S3 provider")
#   deployment.s3.provider        "custom"
#   deployment.s3.endpoint        "https://<account id>.r2.cloudflarestorage.com"
#   deployment.s3.bucket          the bucket name
#   deployment.s3.region          ""                 (the AWS list, unused)
#   deployment.s3.customRegion    "auto"             (R2's region)
# Prefix, ACL and the two Cache-Control headers are left alone: Publii merges
# its defaults over a partial object when it reads the file.

S3_DEPLOYMENT_ARGS = ("website_url", "endpoint", "bucket", "region")
S3_KEY_ARGS = {"id", "key", "access_id", "access_key", "access_key_id", "secret",
               "secret_key", "secret_access_key"}
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def _https_host(value: Any, name: str, bare_host_ok: bool) -> str:
    """'https://<host>' from a URL with no path, query or credentials."""
    if not isinstance(value, str) or not value.strip():
        raise CommandError(f"{name} must be a non-empty string.")
    text = value.strip()
    if bare_host_ok and "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (parts.scheme != "https" or not HOST_RE.match(host) or port is not None
            or parts.username or parts.password or parts.path not in ("", "/")
            or parts.query or parts.fragment):
        hint = (" Give the address alone, e.g. 'example.com'." if bare_host_ok else
                " Give https://<host> alone, without the bucket name or a path.")
        raise CommandError(f"{name}: {value!r} is not an https address.{hint}")
    return "https://" + host


def _check_set_s3_deployment(view, args: dict) -> dict:
    keys = sorted(set(args) & S3_KEY_ARGS)
    if keys:
        raise CommandError(
            f"set_s3_deployment never takes the S3 keys ({keys}): the site owner pastes the "
            f"Access ID and the Secret key in Publii, Server, then clicks Save settings."
        )
    extra = sorted(set(args) - set(S3_DEPLOYMENT_ARGS))
    if extra:
        raise CommandError(f"set_s3_deployment does not take {extra}. Allowed: {list(S3_DEPLOYMENT_ARGS)}.")
    for required in ("website_url", "endpoint", "bucket"):
        if required not in args:
            raise CommandError(f"set_s3_deployment needs {required!r}.")

    website = _https_host(args["website_url"], "website_url", bare_host_ok=True)
    endpoint = _https_host(args["endpoint"], "endpoint", bare_host_ok=False)
    bucket = args["bucket"]
    if not isinstance(bucket, str) or not BUCKET_RE.match(bucket) or ".." in bucket:
        raise CommandError(f"bucket: {bucket!r} is not a bucket name (lowercase letters, digits, "
                           f"hyphens and dots, 3 to 63 characters).")
    region = args.get("region", "auto")
    if not isinstance(region, str) or not REGION_RE.match(region):
        raise CommandError(f"region: {region!r} is not a region name, e.g. 'auto' for Cloudflare R2.")

    deployment = view.site_config().get("deployment") or {}
    current = deployment.get("protocol") or ""
    if current not in ("", "s3"):
        raise CommandError(
            f"The site already publishes with {current!r}. Changing the server type is the "
            f"site owner's decision, in Publii, Server."
        )
    s3 = deployment.get("s3") or {}
    keys_set = bool(s3.get("id")) and bool(s3.get("key"))

    site = {
        ("domain",): website,
        ("deployment", "protocol"): "s3",
        ("deployment", "s3", "customProvider"): True,
        ("deployment", "s3", "provider"): "custom",
        ("deployment", "s3", "endpoint"): endpoint,
        ("deployment", "s3", "bucket"): bucket,
        ("deployment", "s3", "region"): "",
        ("deployment", "s3", "customRegion"): region,
    }
    shown = {
        "website_url": website, "endpoint": endpoint, "bucket": bucket, "region": region,
        "keys": ("already filled in Publii, kept" if keys_set else
                 "not set: the site owner pastes the Access ID and the Secret key in Publii, Server"),
    }
    return {"site": site, "shown": shown}


def _op_set_s3_deployment(repo: PubliiRepository, tx, args: dict) -> Any:
    """Fill Publii's S3 server settings, all but the two keys. args: website_url (the site's address, e.g. 'example.com', written as https://), endpoint (the S3 endpoint without the bucket, e.g. 'https://<account id>.r2.cloudflarestorage.com' for Cloudflare R2), bucket, region? (default 'auto', R2's region). The Access ID and the Secret key are refused: the site owner pastes them in Publii, Server, and clicks Save settings. Keys already filled are kept. A site set to another server type is refused."""
    view = _ApplyView(repo, tx)
    spec = _check_set_s3_deployment(view, args)
    tx.set_config_values(SITE_CONFIG, spec["site"])
    return {"updated": spec["shown"]}


# -- set_theme_style ---------------------------------------------------------- #
# A presentable default look, in two parts.
#
# Theme options go in theme.config.json customConfig, checked against what the
# active theme declares in its own config.json: an option the theme does not
# have is refused, a select or radio value must be one of its options, a colour
# must be a #rrggbb code. Publii merges the theme defaults over whatever
# customConfig leaves out.
#
# The menu style is a stylesheet block written for Publii's default theme,
# "Simple" (3.x: .top header, .navbar__menu, li.active, var(--color) for the
# primary colour). It lives between markers in input/config/custom-css.css, the
# file Publii's Tools, Custom CSS edits, so the rest of that file is never
# touched; "none" removes the block. The last menu entry becomes a button in
# the primary colour with white text, so a colour too light for white text
# (contrast under 3:1, WCAG's floor for large text and controls) is refused.

THEME_STYLE_OPTIONS = {
    "title_alignment": "alignHero",
    "primary_color": "primaryColor",
    "primary_dark_color": "primaryDarkColor",
    "color_scheme": "colorScheme",
    "heading_font": "fontHeadings",
    "body_font": "fontBody",
    "menu_font": "fontMenu",
}
THEME_STYLE_ARGS = (*THEME_STYLE_OPTIONS, "footer_text", "menu_style")
MENU_STYLES = ("pro", "none")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CSS_START = "/* publii-agent-toolkit: menu style, start. Rewritten by set_theme_style. */"
CSS_END = "/* publii-agent-toolkit: menu style, end. */"
MENU_STYLE_CSS = """.top{border-bottom:1px solid rgba(23,24,30,.08);box-shadow:0 2px 12px rgba(23,24,30,.05)}
@media (min-width:56.25em){
.navbar .navbar__menu{align-items:center}
.navbar .navbar__menu>li>a{position:relative;font-weight:600;font-variation-settings:"wght" 600;letter-spacing:.01em;padding:.4rem .9rem}
.navbar .navbar__menu>li:not(:last-child)>a::after{content:"";position:absolute;left:.9rem;right:.9rem;bottom:0;height:2px;border-radius:2px;background:var(--color);transform:scaleX(0);transform-origin:left;transition:transform .25s ease-out}
.navbar .navbar__menu>li:not(:last-child)>a:hover::after,.navbar .navbar__menu>li:not(:last-child)>a:focus-visible::after,.navbar .navbar__menu>li.active:not(:last-child)>a::after{transform:scaleX(1)}
.navbar .navbar__menu>li.active>a{color:var(--color)}
.navbar .navbar__menu>li:last-child{margin-left:.75rem}
.navbar .navbar__menu>li:last-child>a{background:var(--color);color:#fff;border-radius:999px;padding:.55rem 1.4rem;box-shadow:0 2px 10px rgba(0,0,0,.12);transition:filter .2s ease-out,transform .2s ease-out}
.navbar .navbar__menu>li:last-child>a:hover,.navbar .navbar__menu>li:last-child>a:focus{color:#fff;filter:brightness(1.1);transform:translateY(-1px)}
}"""


def _contrast_with_white(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    lum = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return 1.05 / (lum + 0.05)


def _theme_option(manifest: dict, name: str) -> dict | None:
    for option in manifest.get("customConfig") or []:
        if isinstance(option, dict) and option.get("name") == name:
            return option
    return None


def _with_menu_block(css: str, block: str | None) -> str:
    pattern = re.compile(re.escape(CSS_START) + r".*?" + re.escape(CSS_END) + r"\n?", re.S)
    rest = pattern.sub("", css).rstrip()
    if block is None:
        return rest + "\n" if rest else ""
    return (rest + "\n\n" if rest else "") + f"{CSS_START}\n{block}\n{CSS_END}\n"


def _check_set_theme_style(view, args: dict) -> dict:
    extra = sorted(set(args) - set(THEME_STYLE_ARGS))
    if extra:
        raise CommandError(f"set_theme_style does not take {extra}. Allowed: {list(THEME_STYLE_ARGS)}.")
    if not args:
        raise CommandError(f"set_theme_style needs at least one of {list(THEME_STYLE_ARGS)}.")
    manifest = _theme_manifest(view)
    theme_name = str(view.site_config().get("theme") or "")
    theme: dict[tuple[str, ...], Any] = {}
    shown: dict[str, Any] = {}

    for arg, option_name in THEME_STYLE_OPTIONS.items():
        if arg not in args:
            continue
        value = args[arg]
        option = _theme_option(manifest, option_name)
        if option is None:
            raise CommandError(f"{arg}: the theme {theme_name!r} has no {option_name!r} option.")
        kind = option.get("type")
        if kind == "colorpicker":
            if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
                raise CommandError(f"{arg} must be a colour like '#1b5fa8'; got {value!r}.")
            value = value.lower()
        elif kind in ("select", "radio"):
            allowed = [str(o.get("value")) for o in option.get("options") or [] if isinstance(o, dict)]
            if not isinstance(value, str) or value not in allowed:
                raise CommandError(f"{arg}: {value!r} is not one of the theme's choices: {allowed}.")
        else:
            raise CommandError(f"{arg}: the theme's {option_name!r} is a {kind!r} option; not supported.")
        theme[("customConfig", option_name)] = shown[arg] = value

    if "footer_text" in args:
        text = args["footer_text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise CommandError("footer_text must be plain text, 1 to 300 characters.")
        if _theme_option(manifest, "copyrightText") is None:
            raise CommandError(f"footer_text: the theme {theme_name!r} has no 'copyrightText' option.")
        theme[("customConfig", "copyrightText")] = f"<p>{html.escape(text.strip())}</p>"
        shown["footer_text"] = text.strip()

    css = None
    if "menu_style" in args:
        style = args["menu_style"]
        if style not in MENU_STYLES:
            raise CommandError(f"menu_style must be one of {list(MENU_STYLES)}.")
        if style == "pro":
            if theme_name != "simple":
                raise CommandError(f"menu_style 'pro' is written for Publii's default theme 'simple'; "
                                   f"this site uses {theme_name!r}.")
            current = ((view.theme_config().get("customConfig") or {}).get("primaryColor")
                       or (_theme_option(manifest, "primaryColor") or {}).get("value") or "")
            primary = theme.get(("customConfig", "primaryColor"), current)
            if isinstance(primary, str) and HEX_COLOR_RE.match(primary) and _contrast_with_white(primary) < 3:
                raise CommandError(f"The primary colour {primary} is too light for the white text of the "
                                   f"menu button (contrast {_contrast_with_white(primary):.1f}:1, at least 3:1 "
                                   f"needed). Pass a darker primary_color.")
        css = _with_menu_block(view.custom_css(), MENU_STYLE_CSS if style == "pro" else None)
        shown["menu_style"] = style
    return {"theme": theme, "css": css, "shown": shown}


def _op_set_theme_style(repo: PubliiRepository, tx, args: dict) -> Any:
    """Give the site a presentable default look. args, one or more of: title_alignment (the page header, e.g. 'center', one of the theme's choices), primary_color ('#rrggbb'; links, the current menu entry and the menu button), primary_dark_color (the same in dark mode), color_scheme (one of the theme's choices, e.g. 'light' when the logo is dark), heading_font, body_font, menu_font (font names the theme offers, listed in the theme's config.json), footer_text (plain text in place of 'Powered by Publii'), menu_style ('pro': a line under the header, bolder entries underlined in the primary colour on hover and on the current page, the last entry as a button; written for Publii's default theme 'simple'; 'none' removes it). Theme options are checked against the active theme; the menu style goes between markers in input/config/custom-css.css and never touches the rest of that file."""
    view = _ApplyView(repo, tx)
    spec = _check_set_theme_style(view, args)
    if spec["theme"]:
        tx.set_config_values(THEME_CONFIG, spec["theme"])
    if spec["css"] is not None:
        tx.set_custom_css(spec["css"])
    return {"updated": spec["shown"]}


# -- add_media ---------------------------------------------------------------- #

ADD_MEDIA_ARGS = {"file", "target", "slug", "id", "name", "featured", "alt"}


def _check_add_media(view, args: dict) -> dict:
    extra = sorted(set(args) - ADD_MEDIA_ARGS)
    if extra:
        raise CommandError(f"add_media does not take {extra}. Allowed: {sorted(ADD_MEDIA_ARGS)}.")
    if not isinstance(args.get("file"), str) or not args["file"].strip():
        raise CommandError("add_media needs 'file', the path of a local image.")
    target = args.get("target")
    if target not in ("logo", "post"):
        raise CommandError("add_media needs 'target': 'logo' (the site logo) or 'post' (a page or post).")

    source = Path(args["file"]).expanduser()
    if not source.is_file():
        raise CommandError(f"No file at {source}.")
    source = source.resolve()
    size = source.stat().st_size
    if size == 0 or size > MAX_MEDIA_BYTES:
        raise CommandError(f"{source.name} is {size} bytes; images must be 1 byte to "
                           f"{MAX_MEDIA_BYTES // (1024 * 1024)} MB.")
    _check_image(source)

    name = _media_name(args.get("name", source.name))
    if _ext_kind(Path(name).suffix) != _ext_kind(source.suffix):
        raise CommandError(f"name {name!r} must keep the file's extension ({source.suffix}).")

    featured = args.get("featured", False)
    alt = args.get("alt", "")
    if not isinstance(featured, bool) or not isinstance(alt, str):
        raise CommandError("featured must be true or false, and alt a string.")

    post: PostRef | None = None
    if target == "logo":
        if featured or "slug" in args or "id" in args:
            raise CommandError("target 'logo' takes no slug, id or featured.")
        folder = view.media_dir / "website"
        value = f"media/website/{name}"
    else:
        if "slug" not in args and "id" not in args:
            raise CommandError("target 'post' needs the page or post as 'slug' or 'id'.")
        post = view.post(args)
        folder = view.media_dir / "posts" / str(post.id) if post.id is not None else None
        # Publii's own form for an image inside a post or page: the bare file
        # name behind #DOMAIN_NAME#, which it expands to <domain>/media/posts/<id>/.
        value = f"#DOMAIN_NAME#{name}"

    path = (folder / name).resolve() if folder is not None else None
    if path is not None:
        if not path.is_relative_to(view.media_dir.resolve()):
            raise CommandError(f"{name!r} would land outside the site's media folder.")
        if view.media_staged(path):
            raise CommandError(f"{name} is already added in this batch.")
        if path.exists() and path.read_bytes() != source.read_bytes():
            raise CommandError(f"A different file named {name} is already on the site. "
                               f"Pass another 'name'.")
    return {"source": source, "path": path, "name": name, "value": value, "post": post,
            "featured": featured, "alt": alt, "target": target}


def _op_add_media(repo: PubliiRepository, tx, args: dict) -> Any:
    """Copy a local image into the site's media folder and return the value to use. args: file (local path), target ('logo' -> value for update_site_settings.logo; 'post' -> '#DOMAIN_NAME#<file>' to put in the page text), slug|id (target 'post'), name? (file name on the site), featured? (true: make it the page's featured image), alt?. JPEG, PNG, WebP, GIF or SVG only; never overwrites a different file. For a page or post, also makes the smaller copies the theme's srcset expects (responsive/<name>-<size>), as Publii does when an image is added in the app, when Pillow is installed; 'responsive' in the result says what was made or why not."""
    spec = _check_add_media(_ApplyView(repo, tx), args)
    post: PostRef | None = spec["post"]
    if post is not None:
        path = (repo.paths.media_dir / "posts" / str(post.id) / spec["name"]).resolve()
    else:
        path = spec["path"]
    copied = tx.stage_media(spec["source"], path)
    image_id = None
    if spec["featured"]:
        image_id = tx.add_post_image(post.id, url=spec["name"], alt=spec["alt"], set_as_featured=True)
    responsive = (_stage_responsive(_ApplyView(repo, tx), tx, spec["source"], path, spec["featured"])
                  if post is not None else None)
    return {"value": spec["value"], "file": spec["name"],
            "path": str(path.relative_to(repo.paths.input_dir.resolve()).as_posix()),
            "copied": copied, "featured_image_id": image_id,
            "post_id": post.id if post is not None else None,
            "responsive": responsive}


def _responsive_plan(view, name: str, featured: bool) -> tuple[dict[str, int], list[str], str]:
    """Sizes to make for a post image, the sizes left to Publii, and why none, if none."""
    advanced = view.site_config().get("advanced") or {}
    if not advanced.get("responsiveImages", 1):
        return {}, [], "responsive images are turned off in the site settings"
    if advanced.get("forceWebp"):
        return {}, [], ("the site converts images to WebP; regenerate thumbnails in Publii "
                        "(Tools) before publishing")
    if Path(name).suffix.lower() not in images.RESIZABLE:
        return {}, [], f"Publii makes no smaller copies of {Path(name).suffix} images"
    widths, skipped = images.variant_widths(_theme_manifest(view), featured)
    if not widths:
        return {}, skipped, "the theme declares no responsive sizes for this image"
    if not images.pillow_available():
        return {}, skipped, ("Pillow is not installed (pip install Pillow), so no smaller copies "
                             "were made; regenerate thumbnails in Publii (Tools) before publishing")
    return widths, skipped, ""


def _stage_responsive(view, tx, source: Path, path: Path, featured: bool) -> dict:
    """Make the theme's smaller copies of a post image and stage them with it.

    A copy already on disk (made by Publii, or by an earlier run) is kept as is:
    stage_media never overwrites a media file, and Publii's own copy is as good.
    """
    widths, left_to_publii, why = _responsive_plan(view, path.name, featured)
    out: dict[str, Any] = {"made": [], "kept": [], "left_to_publii": left_to_publii}
    if why:
        out["note"] = why
        return out
    advanced = view.site_config().get("advanced") or {}
    quality = advanced.get("imagesQuality") or 60
    folder = path.parent / "responsive"
    with tempfile.TemporaryDirectory(prefix="publii-responsive-") as tmp:
        for size, made, (w, h) in images.make_variants(source, Path(tmp), path.stem, path.suffix,
                                                        widths, quality):
            target = folder / made.name
            if target.exists():
                out["kept"].append(size)
                continue
            tx.stage_media(made, target)
            out["made"].append(f"{size} {w}x{h}")
    return out


def _ext_kind(suffix: str) -> str:
    suffix = suffix.lower()
    return "jpeg" if suffix in (".jpg", ".jpeg") else suffix.lstrip(".")


def _media_name(raw: Any) -> str:
    """A file name Publii keeps as is, or a refusal. Never a path."""
    if not isinstance(raw, str) or not raw.strip():
        raise CommandError("name must be a non-empty file name.")
    if any(sep in raw for sep in ("/", "\\", ":")) or ".." in raw:
        raise CommandError(f"name {raw!r} must be a bare file name, without folders or '..'.")
    name = unicodedata.normalize("NFKD", raw.strip()).encode("ascii", "ignore").decode()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    if not MEDIA_NAME_RE.match(name) or ".." in name or Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        raise CommandError(f"{raw!r} is not a usable image file name.")
    return name


def _check_image(path: Path) -> None:
    """Refuse anything whose bytes are not the image its extension claims."""
    kind = _ext_kind(path.suffix)
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise CommandError(f"{path.name}: only {', '.join(IMAGE_EXTENSIONS)} images are accepted.")
    head = path.read_bytes()[:16]
    sniffed = None
    if head.startswith(b"\xff\xd8\xff"):
        sniffed = "jpeg"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        sniffed = "png"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        sniffed = "gif"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        sniffed = "webp"
    elif kind == "svg":
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff \t\r\n").lower()
        if text.startswith(("<svg", "<?xml", "<!--", "<!doctype svg")) and "<svg" in text:
            # An SVG is a document that can carry script. A logo never needs one.
            if re.search(r"<script|javascript:|\son[a-z]+\s*=|<foreignobject", text):
                raise CommandError(f"{path.name} contains script or event handlers; refused.")
            sniffed = "svg"
    if sniffed != kind:
        raise CommandError(f"{path.name} is not a {kind.upper()} image (its content says "
                           f"{sniffed or 'something else'}).")


READ_OPS: dict[str, Callable] = {
    "list_posts": _op_list_posts,
    "get_post": _op_get_post,
    "list_tags": _op_list_tags,
    "list_menus": _op_list_menus,
    "get_site_settings": _op_get_site_settings,
    "check": _op_check,
}

WRITE_OPS: dict[str, Callable] = {
    "create_page": _op_create_page,
    "update_post": _op_update_post,
    "update_meta": _op_update_meta,
    "set_tags": _op_set_tags,
    "create_menu": _op_create_menu,
    "add_menu_item": _op_add_menu_item,
    "remove_menu_item": _op_remove_menu_item,
    "update_menu_item": _op_update_menu_item,
    "update_site_settings": _op_update_site_settings,
    "set_s3_deployment": _op_set_s3_deployment,
    "set_theme_style": _op_set_theme_style,
    "add_media": _op_add_media,
}

OPERATIONS = {**READ_OPS, **WRITE_OPS}


# ----------------------------------------------------------------- the session


def publii_is_running() -> bool:
    """True when the Publii desktop app is open (Windows; elsewhere, unknown -> False).

    Publii keeps a site's configuration in memory and writes it back when its
    settings are saved, so a change made under it can be silently overwritten.
    The database lock does not cover that: a config-only change never touches
    a row. Hence a process check before any apply.
    """
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Publii.exe", "/NH"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Publii.exe" in (out or "")


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
        a slug does not exist before anything is locked or backed up. Steps are
        checked in order against the site plus what earlier steps would create,
        so a page made in step 1 can be linked from a menu in step 2.

        A problem in one step marks the whole plan not-ok, because ``apply`` is
        all-or-nothing: a batch containing one bad command lands none of them.
        Every step is still described, so the agent can fix all the problems in
        one pass instead of discovering them one at a time.
        """
        steps: list[dict[str, Any]] = []
        problems: list[str] = []
        view = _PlanView(self.repo)

        for index, payload in enumerate(payloads):
            cmd = Command.parse(payload)
            if cmd.op in READ_OPS:
                raise CommandError(f"{cmd.op!r} is a read. Use read() for it.")

            step: dict[str, Any] = {"index": index, "op": cmd.op, "args": cmd.args}
            try:
                step["describes"] = _describe_or_raise(self.repo, cmd, view)
            except (CommandError, ValueError) as exc:
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
        if publii_is_running():
            return _err("publii_running",
                        "Publii is open. Close it, then apply again: it would overwrite "
                        "configuration changes with the copy it holds in memory.")

        results = []
        backup = None
        try:
            with self.repo.transaction() as tx:
                backup = tx.backup_dir
                for cmd in commands:
                    results.append(
                        {"op": cmd.op, "result": WRITE_OPS[cmd.op](self.repo, tx, cmd.args)}
                    )
        except PubliiLockedError as exc:
            return _err("site_locked", str(exc))
        except PubliiIntegrityError as exc:
            return _err("integrity_failed", str(exc))
        except (CommandError, ValueError) as exc:
            # ValueError is what the transaction raises for a target it cannot
            # find (an unknown tag slug, a missing parent). Same meaning, same code.
            return _err("bad_command", str(exc))
        except OSError as exc:
            return _err("io_error", str(exc))

        return _ok({"applied": results, "count": len(results),
                    "backup": str(backup) if backup else None})


# --------------------------------------------------------------------- helpers


def _resolve_post(repo: PubliiRepository, args: dict) -> Post:
    if "slug" in args:
        post = repo.get_post_by_slug(str(args["slug"]))
        if post is None:
            raise CommandError(f"No post or page with slug {args['slug']!r}.")
        return post
    if "id" in args:
        post = repo.get_post(_as_int(args["id"], "id"))
        if post is None:
            raise CommandError(f"No post or page with id {args['id']}.")
        return post
    raise CommandError('Identify the post with "slug" or "id".')


def _resolve_post_tx(tx, args: dict) -> Post:
    """Like _resolve_post, but sees pages created earlier in the same transaction."""
    if "slug" in args:
        post = tx.find_post(slug=str(args["slug"]))
        if post is None:
            raise CommandError(f"No post or page with slug {args['slug']!r}.")
        return post
    if "id" in args:
        post = tx.find_post(post_id=_as_int(args["id"], "id"))
        if post is None:
            raise CommandError(f"No post or page with id {args['id']}.")
        return post
    raise CommandError('Identify the post with "slug" or "id".')


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CommandError(f"{name} must be an integer, not {value!r}.") from None


def _describe_or_raise(repo: PubliiRepository, cmd: Command, view: _PlanView) -> str:
    """A one-line summary of what a command would do.

    Raises CommandError when the command cannot run at all, which is how
    ``plan`` finds problems without opening a transaction. A step that passes
    records what it would create in ``view``, for the steps after it.
    """
    if cmd.op in {"update_post", "update_meta", "set_tags"}:
        post = view.post(cmd.args)
        fields = sorted(k for k in cmd.args if k not in {"slug", "id"})
        if not fields:
            raise CommandError(f"{cmd.op} needs at least one field to change.")
        where = f"id {post.id}" if post.id is not None else "created earlier in this batch"
        return f"{cmd.op} on {post.slug!r} ({where}): {', '.join(fields)}"

    if cmd.op == "create_page":
        spec = _check_create_page(view, cmd.args)
        view.pending_pages[spec["slug"]] = spec["editor"]
        under = f" under {spec['parent'].slug!r}" if spec["parent"] else ""
        return (f"create {spec['status']} page {spec['slug']!r} ({spec['editor']}, "
                f"{len(spec['text'])} chars){under}, registered in pages.config.json")

    if cmd.op == "create_menu":
        spec = _check_create_menu(view, cmd.args)
        view.pending_menus.add(spec["position"])
        return f"create menu {spec['name']!r} at position {spec['position']!r}"

    if cmd.op == "add_menu_item":
        spec = _check_add_menu_item(view, cmd.args)
        target = (f"page {spec['ref'].slug!r}" if spec["ref"] is not None
                  else f"link {cmd.args['link']!r}")
        return f"add {cmd.args['label']!r} to menu {cmd.args['position']!r} -> {target}"

    if cmd.op == "update_site_settings":
        spec = _check_update_site_settings(view, cmd.args)
        return "update site settings: " + ", ".join(
            f"{k} -> {v!r}" for k, v in spec["shown"].items())

    if cmd.op == "set_theme_style":
        spec = _check_set_theme_style(view, cmd.args)
        return "set the site's look: " + ", ".join(f"{k} -> {v!r}" for k, v in spec["shown"].items())

    if cmd.op == "set_s3_deployment":
        spec = _check_set_s3_deployment(view, cmd.args)
        return "set the S3 server settings: " + ", ".join(
            f"{k} -> {v!r}" for k, v in spec["shown"].items())

    if cmd.op == "add_media":
        spec = _check_add_media(view, cmd.args)
        if spec["path"] is not None:
            view.pending_media.add(spec["path"])
        where = ("media/website" if spec["target"] == "logo" else
                 f"media/posts/{spec['post'].id if spec['post'].id is not None else '<new id>'}")
        extra = " and make it the featured image" if spec["featured"] else ""
        if spec["target"] == "post":
            widths, _, why = _responsive_plan(view, spec["name"], spec["featured"])
            extra += (f", with {len(widths)} smaller copies for the theme ({', '.join(widths)})"
                      if widths else f" ({why})")
        return f"copy {spec['source'].name} to {where}/{spec['name']}{extra}; use {spec['value']!r}"

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
