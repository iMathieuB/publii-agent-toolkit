"""Unified read/write repository for a Publii site, with a Unit-of-Work
transaction (``PubliiTransaction``) guaranteeing atomic SQLite + JSON writes.

Responsibilities (merged from the former reader / writer / validator design):
  * READ   -> typed Pydantic models (validation on read).
  * INTEGRITY -> ``PubliiRepository.check_integrity`` (relational checks).
  * WRITE  -> ``PubliiRepository.transaction()`` -> ``PubliiTransaction``.

Every mutation goes through ``PubliiTransaction``, which:
  1. acquires the SQLite write lock first (fails fast if Publii has the DB open),
  2. backs up the mutable surface (db.sqlite + config/) before any change,
  3. buffers SQLite work in one transaction and stages JSON to temp files,
  4. on success: runs the integrity gate, COMMITs, then atomically swaps JSON,
  5. on any exception or integrity ERROR: ROLLBACK + delete temp JSON +
     restore input/ from the backup, then re-raises.

Verified against the live sites; no invented Publii fields or parameters.
Requires: Python 3.10+, pydantic>=2 (stdlib otherwise).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    CoreMeta,
    IntegrityIssue,
    Menu,
    MenuItem,
    Post,
    PostAdditionalData,
    SitePaths,
    Tag,
)

LOGGER = logging.getLogger("publii")

# Expected schema (table -> required columns). Used by the schema integrity check.
EXPECTED_SCHEMA: dict[str, set[str]] = {
    "posts": {
        "id", "title", "authors", "slug", "text", "featured_image_id",
        "created_at", "modified_at", "status", "template",
    },
    "posts_additional_data": {"id", "post_id", "key", "value"},
    "posts_images": {"id", "post_id", "url", "title", "caption", "additional_data"},
    "posts_tags": {"tag_id", "post_id"},
    "tags": {"id", "name", "slug", "description", "additional_data"},
    "authors": {"id", "name", "username", "password", "config", "additional_data"},
}

# Config keys touched by writes (backed up / restored as the mutable surface).
MENU_CONFIG = "menu.config.json"
PAGES_CONFIG = "pages.config.json"


def now_ms() -> int:
    """Current time as epoch milliseconds (Publii's timestamp unit)."""
    return int(time.time() * 1000)


def _parse_json_field(raw: Any) -> dict:
    """Parse a JSON-string DB field into a dict; empty/None -> {}."""
    if not raw:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class PubliiLockedError(RuntimeError):
    """Raised when the SQLite database is locked (Publii likely running)."""


class PubliiIntegrityError(RuntimeError):
    """Raised by the commit gate when integrity checks return ERROR issues."""

    def __init__(self, issues: list[IntegrityIssue]) -> None:
        self.issues = issues
        super().__init__(
            "Integrity gate failed:\n"
            + "\n".join(f"  [{i.severity}] {i.code}: {i.detail}" for i in issues)
        )


# --------------------------------------------------------------------------- #
# Integrity evaluation (shared by repo.check_integrity and the commit gate)
# --------------------------------------------------------------------------- #
def _iter_menu_items(items: list[MenuItem]):
    for it in items:
        yield it
        yield from _iter_menu_items(it.items)


def evaluate_integrity(
    conn: sqlite3.Connection,
    menus: list[Menu],
    pages_config: list[dict],
) -> list[IntegrityIssue]:
    """Run all relational integrity checks against an open connection plus the
    parsed menu and pages configs. Works on uncommitted state inside a txn."""
    issues: list[IntegrityIssue] = []
    cur = conn.cursor()

    # 1. Schema presence
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    for table, cols in EXPECTED_SCHEMA.items():
        if table not in tables:
            issues.append(IntegrityIssue(code="schema.missing_table", severity="ERROR",
                                         detail=f"Missing table: {table}"))
            continue
        cur.execute(f"PRAGMA table_info({table})")
        present = {r[1] for r in cur.fetchall()}
        missing = cols - present
        if missing:
            issues.append(IntegrityIssue(code="schema.missing_columns", severity="ERROR",
                                         detail=f"{table} missing columns: {sorted(missing)}"))

    # 2. Unique slugs
    cur.execute("SELECT slug, COUNT(*) c FROM posts GROUP BY slug HAVING c > 1")
    for slug, c in cur.fetchall():
        issues.append(IntegrityIssue(code="posts.duplicate_slug", severity="ERROR",
                                     detail=f"Duplicate slug '{slug}' ({c} rows)"))

    # Gather id sets
    cur.execute("SELECT id, status FROM posts")
    rows = cur.fetchall()
    all_post_ids = {r[0] for r in rows}
    page_ids = {r[0] for r in rows if "is-page" in (r[1] or "")}
    cur.execute("SELECT id FROM tags")
    tag_ids = {r[0] for r in cur.fetchall()}

    # 3. Menu links resolve
    for menu in menus:
        for item in _iter_menu_items(menu.items):
            if item.type in ("page", "post") and isinstance(item.link, int):
                if item.link not in all_post_ids:
                    issues.append(IntegrityIssue(
                        code="menu.dangling_link", severity="ERROR",
                        detail=f"Menu '{menu.position}' item '{item.label}' links to "
                               f"missing post id {item.link}"))

    # 4. pages.config <-> is-page consistency
    config_ids = {entry.get("id") for entry in pages_config}
    for cid in config_ids:
        if cid not in page_ids:
            issues.append(IntegrityIssue(code="pages_config.not_a_page", severity="ERROR",
                                         detail=f"pages.config id {cid} is not an is-page row"))
    for pid in page_ids:
        if pid not in config_ids:
            issues.append(IntegrityIssue(code="pages_config.unlisted_page", severity="WARN",
                                         detail=f"is-page id {pid} not present in pages.config"))

    # 5. _core.mainTag references
    cur.execute("SELECT post_id, value FROM posts_additional_data WHERE key='_core'")
    for post_id, value in cur.fetchall():
        core = _parse_json_field(value)
        main = core.get("mainTag")
        if main not in (None, ""):
            try:
                tid = int(main)
            except (TypeError, ValueError):
                issues.append(IntegrityIssue(code="core.maintag_not_int", severity="ERROR",
                                             detail=f"post {post_id} mainTag '{main}' not an int id"))
                continue
            if tid not in tag_ids:
                issues.append(IntegrityIssue(code="core.maintag_missing", severity="ERROR",
                                             detail=f"post {post_id} mainTag {tid} not in tags"))

    # 6. Orphan junction rows
    cur.execute("SELECT tag_id, post_id FROM posts_tags")
    for tag_id, post_id in cur.fetchall():
        if post_id not in all_post_ids:
            issues.append(IntegrityIssue(code="posts_tags.orphan_post", severity="ERROR",
                                         detail=f"posts_tags references missing post {post_id}"))
        if tag_id not in tag_ids:
            issues.append(IntegrityIssue(code="posts_tags.orphan_tag", severity="ERROR",
                                         detail=f"posts_tags references missing tag {tag_id}"))

    return issues


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class PubliiRepository:
    """Read access + integrity checks + transaction factory for one site."""

    def __init__(self, paths: SitePaths, *, logger: logging.Logger | None = None) -> None:
        self.paths = paths
        self.log = logger or LOGGER

    @classmethod
    def open(cls, site_root: str | Path, **kw) -> "PubliiRepository":
        return cls(SitePaths.from_root(site_root), **kw)

    # -- connections --------------------------------------------------------- #
    def _connect_ro(self) -> sqlite3.Connection:
        uri = f"file:{self.paths.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    # -- reads (return validated models) ------------------------------------ #
    def list_posts(self, *, pages: bool | None = None) -> list[Post]:
        with self._connect_ro() as conn:
            rows = conn.execute("SELECT * FROM posts ORDER BY id").fetchall()
        posts = [Post.model_validate(dict(r)) for r in rows]
        if pages is True:
            return [p for p in posts if p.is_page]
        if pages is False:
            return [p for p in posts if not p.is_page]
        return posts

    def get_post(self, post_id: int) -> Post | None:
        with self._connect_ro() as conn:
            row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return Post.model_validate(dict(row)) if row else None

    def get_post_by_slug(self, slug: str) -> Post | None:
        with self._connect_ro() as conn:
            row = conn.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
        return Post.model_validate(dict(row)) if row else None

    def get_additional_data(self, post_id: int) -> PostAdditionalData:
        payload: dict[str, Any] = {}
        with self._connect_ro() as conn:
            rows = conn.execute(
                "SELECT key, value FROM posts_additional_data WHERE post_id=?", (post_id,)
            ).fetchall()
        for r in rows:
            payload[r["key"]] = _parse_json_field(r["value"])
        return PostAdditionalData.model_validate(payload)

    def list_tags(self) -> list[Tag]:
        with self._connect_ro() as conn:
            rows = conn.execute("SELECT * FROM tags ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["additional_data"] = _parse_json_field(d.get("additional_data"))
            out.append(Tag.model_validate(d))
        return out

    def get_tag_by_slug(self, slug: str) -> Tag | None:
        for t in self.list_tags():
            if t.slug == slug:
                return t
        return None

    def get_post_tag_ids(self, post_id: int) -> list[int]:
        with self._connect_ro() as conn:
            rows = conn.execute(
                "SELECT tag_id FROM posts_tags WHERE post_id=? ORDER BY tag_id", (post_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def get_post_images(self, post_id: int) -> list[dict]:
        with self._connect_ro() as conn:
            rows = conn.execute(
                "SELECT * FROM posts_images WHERE post_id=? ORDER BY id", (post_id,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["additional_data"] = _parse_json_field(d.get("additional_data"))
            result.append(d)
        return result

    def read_menus(self) -> list[Menu]:
        data = self._read_json(self.paths.config_dir / MENU_CONFIG, default=[])
        return [Menu.model_validate(m) for m in data]

    def read_pages_config(self) -> list[dict]:
        return self._read_json(self.paths.config_dir / PAGES_CONFIG, default=[])

    def read_site_config(self) -> dict:
        return self._read_json(self.paths.config_dir / "site.config.json", default={})

    def next_post_id(self) -> int:
        """Next id SQLite would assign to ``posts`` (seq + 1)."""
        with self._connect_ro() as conn:
            row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='posts'"
            ).fetchone()
        return (row[0] + 1) if row else 1

    @staticmethod
    def _read_json(path: Path, *, default):
        if not path.is_file():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # -- integrity (read-only, standalone) ---------------------------------- #
    def check_integrity(self) -> list[IntegrityIssue]:
        with self._connect_ro() as conn:
            return evaluate_integrity(conn, self.read_menus(), self.read_pages_config())

    # -- write entrypoint ---------------------------------------------------- #
    def transaction(self) -> "PubliiTransaction":
        return PubliiTransaction(self, logger=self.log)


# --------------------------------------------------------------------------- #
# Unit of Work
# --------------------------------------------------------------------------- #
class PubliiTransaction:
    """Atomic, rollback-safe unit of work over SQLite + config JSON.

    Usage::

        with repo.transaction() as tx:
            tx.update_post_fields(2, title="...")
        # commit + atomic JSON swap on clean exit; full rollback on any error.
    """

    def __init__(self, repo: PubliiRepository, *, logger: logging.Logger | None = None) -> None:
        self.repo = repo
        self.paths = repo.paths
        self.log = logger or LOGGER
        self._conn: sqlite3.Connection | None = None
        self._backup_dir: Path | None = None
        self._staged: dict[Path, Path] = {}      # target -> tmp path
        self._json_cache: dict[Path, Any] = {}   # target -> current (staged) data
        self._db_committed = False

    # -- context management -------------------------------------------------- #
    def __enter__(self) -> "PubliiTransaction":
        # 1. Acquire write lock FIRST (fail fast if Publii has the DB open).
        try:
            self._conn = sqlite3.connect(str(self.paths.db_path), timeout=2.0,
                                         isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if self._conn:
                self._conn.close()
                self._conn = None
            raise PubliiLockedError(
                f"Cannot acquire write lock on {self.paths.db_path}. "
                f"Close Publii and retry. ({exc})"
            ) from exc

        # 2. Backup mutable surface (db.sqlite + config/) before any change.
        try:
            self._backup_dir = self._backup_input()
            self.log.info("Backup created: %s", self._backup_dir)
        except Exception:
            self._conn.execute("ROLLBACK")
            self._conn.close()
            self._conn = None
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None:
                # Exception raised inside the with-block.
                self._abort(exc)
                return False  # propagate original exception
            # Success path: integrity gate, then commit, then atomic JSON swap.
            issues = evaluate_integrity(self._conn, self._current_menus(),
                                        self._current_pages_config())
            errors = [i for i in issues if i.severity == "ERROR"]
            if errors:
                raise PubliiIntegrityError(errors)
            self._conn.execute("COMMIT")
            self._db_committed = True
            for target, tmp in self._staged.items():
                os.replace(str(tmp), str(target))
            self.log.info("Committed; %d JSON file(s) swapped atomically.", len(self._staged))
            return False
        except BaseException as e:
            self._abort(e)
            if exc_type is None:
                raise  # integrity / commit error originated in __exit__
            return False
        finally:
            self._close()

    # -- safety primitives --------------------------------------------------- #
    def _backup_input(self) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        parent = self.paths.input_dir.parent / "input_backup"
        backup_root = parent / ts
        # Two transactions in the same second used to share one folder, and the
        # second backup overwrote the first. Never reuse a folder.
        n = 2
        while backup_root.exists():
            backup_root = parent / f"{ts}-{n}"
            n += 1
        backup_root.mkdir(parents=True)
        shutil.copy2(self.paths.db_path, backup_root / "db.sqlite")
        if self.paths.config_dir.is_dir():
            shutil.copytree(self.paths.config_dir, backup_root / "config", dirs_exist_ok=True)
        return backup_root

    def _abort(self, error: BaseException) -> None:
        self.log.error("Aborting transaction: %s", error)
        # a. SQLite ROLLBACK
        try:
            if self._conn is not None:
                self._conn.execute("ROLLBACK")
        except Exception as e:  # noqa: BLE001
            self.log.warning("ROLLBACK failed (continuing to restore): %s", e)
        # b. delete staged temp JSON
        for tmp in self._staged.values():
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        # c. close DB handle so files can be replaced on Windows, then restore.
        self._close()
        self._restore_input()

    def _restore_input(self) -> None:
        if not self._backup_dir:
            return
        self.log.warning("Restoring input/ from backup: %s", self._backup_dir)
        db_backup = self._backup_dir / "db.sqlite"
        if db_backup.is_file():
            shutil.copy2(db_backup, self.paths.db_path)
        cfg_backup = self._backup_dir / "config"
        if cfg_backup.is_dir():
            shutil.copytree(cfg_backup, self.paths.config_dir, dirs_exist_ok=True)

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- staged JSON helpers ------------------------------------------------- #
    def _current_json(self, path: Path, *, default):
        if path in self._json_cache:
            return self._json_cache[path]
        if path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return default

    def _stage_json(self, path: Path, data: Any) -> None:
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        self._staged[path] = tmp
        self._json_cache[path] = data

    def _current_menus(self) -> list[Menu]:
        raw = self._current_json(self.paths.config_dir / MENU_CONFIG, default=[])
        return [Menu.model_validate(m) for m in raw]

    def _current_pages_config(self) -> list[dict]:
        return self._current_json(self.paths.config_dir / PAGES_CONFIG, default=[])

    # -- write operations (SQLite buffered; JSON staged) -------------------- #
    def update_post_fields(self, post_id: int, *, title: str | None = None,
                           text: str | None = None, slug: str | None = None,
                           status: str | None = None) -> None:
        sets: dict[str, Any] = {}
        if title is not None:
            sets["title"] = title
        if text is not None:
            sets["text"] = text
        if slug is not None:
            sets["slug"] = slug
        if status is not None:
            sets["status"] = status
        if not sets:
            return
        sets["modified_at"] = now_ms()
        clause = ", ".join(f"{k}=?" for k in sets)
        self._conn.execute(f"UPDATE posts SET {clause} WHERE id=?",
                           [*sets.values(), post_id])
        self.log.info("Staged update on post %s: %s", post_id, list(sets))

    def upsert_additional_data(self, post_id: int, data: PostAdditionalData) -> None:
        """Write each present component of the additional-data model.

        ``mode="json"`` + ``exclude_none`` keeps the on-disk shape close to
        Publii's and avoids writing null keys Publii never had.
        """
        components: dict[str, dict | None] = {
            "_core": data.core.model_dump(mode="json", exclude_none=True),
            "postViewSettings": (data.postViewSettings.model_dump(mode="json")
                                 if data.postViewSettings is not None else None),
            "pageViewSettings": (data.pageViewSettings.model_dump(mode="json")
                                 if data.pageViewSettings is not None else None),
        }
        for key, value in components.items():
            if value is None:
                continue
            encoded = json.dumps(value, ensure_ascii=False)
            row = self._conn.execute(
                "SELECT id FROM posts_additional_data WHERE post_id=? AND key=?",
                (post_id, key),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE posts_additional_data SET value=? WHERE id=?",
                    (encoded, row["id"]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO posts_additional_data (post_id, key, value) VALUES (?,?,?)",
                    (post_id, key, encoded),
                )
        self.log.info("Staged additional_data upsert for post %s", post_id)

    def create_post(self, post: Post, *, core: CoreMeta | None = None,
                    register_as_page: bool = False) -> int:
        """Insert a new posts row. ``post.id`` is ignored (AUTOINCREMENT assigns it).
        Returns the new id. If ``register_as_page`` is True, also registers it in
        pages.config.json within the same transaction."""
        created = post.created_at or now_ms()
        modified = post.modified_at or created
        status = post.status
        if register_as_page and "is-page" not in status:
            status = (status + ",is-page") if status else "published,is-page"
        cur = self._conn.execute(
            "INSERT INTO posts (title, authors, slug, text, featured_image_id, "
            "created_at, modified_at, status, template) VALUES (?,?,?,?,?,?,?,?,?)",
            (post.title, post.authors, post.slug, post.text, post.featured_image_id,
             created, modified, status, post.template),
        )
        new_id = int(cur.lastrowid)
        if core is not None:
            self.upsert_additional_data(new_id, PostAdditionalData(core=core))
        if register_as_page:
            self.register_page(new_id)
        self.log.info("Staged create_post -> new id %s (slug=%s)", new_id, post.slug)
        return new_id

    def register_page(self, post_id: int, parent_id: int | None = None) -> None:
        cfg = list(self._current_pages_config())
        if any(e.get("id") == post_id for e in cfg):
            return
        entry = {"id": post_id, "subpages": []}
        if parent_id is None:
            cfg.append(entry)
        else:
            placed = False
            for e in cfg:
                if e.get("id") == parent_id:
                    e.setdefault("subpages", []).append(entry)
                    placed = True
                    break
            if not placed:
                raise ValueError(f"Parent page id {parent_id} not found in pages.config")
        self._stage_json(self.paths.config_dir / PAGES_CONFIG, cfg)
        self.log.info("Staged pages.config registration for page %s", post_id)

    def set_post_tags(self, post_id: int, tag_slugs: list[str]) -> None:
        """Replace a post's tags, resolving slugs -> ids ON THIS SITE.

        Raises ValueError on an unknown slug (v1 never auto-creates tags, and
        never copies tag ids across sites -- see design doc 1.5)."""
        resolved: list[int] = []
        for slug in tag_slugs:
            row = self._conn.execute("SELECT id FROM tags WHERE slug=?", (slug,)).fetchone()
            if not row:
                raise ValueError(f"Tag slug '{slug}' not found on this site")
            resolved.append(row["id"])
        self._conn.execute("DELETE FROM posts_tags WHERE post_id=?", (post_id,))
        for tid in resolved:
            self._conn.execute(
                "INSERT INTO posts_tags (tag_id, post_id) VALUES (?,?)", (tid, post_id)
            )
        self.log.info("Staged tags for post %s: %s", post_id, resolved)

    def add_post_image(self, post_id: int, *, url: str, alt: str = "",
                       caption: str = "", credits: str = "",
                       set_as_featured: bool = False) -> int:
        """Insert a ``posts_images`` row and optionally mark it as the featured image.

        The row shape mirrors rows VERIFIED on the live sites (pages 11, 18, 19):
        ``url`` is the bare file name, the ``title`` and ``caption`` COLUMNS stay
        empty strings, and alt / caption / credits live in the ``additional_data``
        JSON. Nothing here is invented.

        The image FILE itself is not handled: copy ``url`` into
        ``input/media/posts/<post_id>/`` (plus the ``responsive/`` variants) before
        calling this, so a rollback can never leave a row pointing at a missing file.

        Returns the new ``posts_images.id``.
        """
        row = self._conn.execute(
            "SELECT id FROM posts_images WHERE post_id=? AND url=?",
            (post_id, url),
        ).fetchone()
        if row:
            raise ValueError(
                f"posts_images already has url '{url}' for post {post_id} (id {row['id']})")

        additional = json.dumps({"alt": alt, "caption": caption, "credits": credits},
                                ensure_ascii=False)
        cur = self._conn.execute(
            "INSERT INTO posts_images (post_id, url, title, caption, additional_data) "
            "VALUES (?,?,?,?,?)",
            (post_id, url, "", "", additional),
        )
        image_id = int(cur.lastrowid)
        if set_as_featured:
            self._conn.execute(
                "UPDATE posts SET featured_image_id=?, modified_at=? WHERE id=?",
                (image_id, now_ms(), post_id),
            )
        self.log.info("Staged image '%s' for post %s -> posts_images id %s%s",
                      url, post_id, image_id, " (featured)" if set_as_featured else "")
        return image_id

    def update_post_image(self, image_id: int, *, url: str, alt: str = "",
                          caption: str = "", credits: str = "",
                          set_as_featured_on: int | None = None) -> None:
        """Fill an EXISTING ``posts_images`` row.

        Publii inserts an empty row (``url = ''``) as soon as a post is opened in
        its editor. Filling that row is correct; inserting a second one would leave
        the empty one orphaned. Same verified shape as ``add_post_image``.
        """
        row = self._conn.execute(
            "SELECT id, post_id, url FROM posts_images WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise ValueError(f"posts_images id {image_id} not found")

        additional = json.dumps({"alt": alt, "caption": caption, "credits": credits},
                                ensure_ascii=False)
        self._conn.execute(
            "UPDATE posts_images SET url=?, title=?, caption=?, additional_data=? WHERE id=?",
            (url, "", "", additional, image_id),
        )
        if set_as_featured_on is not None:
            self._conn.execute(
                "UPDATE posts SET featured_image_id=?, modified_at=? WHERE id=?",
                (image_id, now_ms(), set_as_featured_on),
            )
        self.log.info("Staged image update id %s -> url='%s' (was '%s')",
                      image_id, url, row["url"])

    def inject_menu_item(self, position: str, item: MenuItem,
                         *, parent_id: int | None = None) -> int:
        menus = self._current_json(self.paths.config_dir / MENU_CONFIG, default=[])
        target_menu = next((m for m in menus if m.get("position") == position), None)
        if target_menu is None:
            raise ValueError(f"Menu position '{position}' not found")
        if not item.id:
            item = item.model_copy(update={"id": now_ms()})
        item_dict = item.model_dump(mode="json")
        if parent_id is None:
            target_menu.setdefault("items", []).append(item_dict)
        else:
            if not _attach_to_parent(target_menu.get("items", []), parent_id, item_dict):
                raise ValueError(f"Parent menu item id {parent_id} not found in '{position}'")
        self._stage_json(self.paths.config_dir / MENU_CONFIG, menus)
        self.log.info("Staged menu injection into '%s' (item id %s)", position, item.id)
        return int(item.id)

    def remove_menu_item(self, position: str, item_id: int) -> None:
        menus = self._current_json(self.paths.config_dir / MENU_CONFIG, default=[])
        target_menu = next((m for m in menus if m.get("position") == position), None)
        if target_menu is None:
            raise ValueError(f"Menu position '{position}' not found")
        if not _remove_by_id(target_menu.get("items", []), item_id):
            raise ValueError(f"Menu item id {item_id} not found in '{position}'")
        self._stage_json(self.paths.config_dir / MENU_CONFIG, menus)
        self.log.info("Staged menu removal of id %s from '%s'", item_id, position)

    def update_menu_item(self, position: str, item_id: int, **fields: Any) -> None:
        """Change fields of one menu item in place, its label for instance.

        The item keeps its position. Removing it and injecting a copy would move
        it to the end of its level, which is why renaming needs its own method.
        """
        refused = sorted((set(fields) & {"id", "items"}) | (set(fields) - set(MenuItem.model_fields)))
        if refused:
            raise ValueError(f"Cannot update menu item field(s) {refused}")
        menus = self._current_json(self.paths.config_dir / MENU_CONFIG, default=[])
        target_menu = next((m for m in menus if m.get("position") == position), None)
        if target_menu is None:
            raise ValueError(f"Menu position '{position}' not found")
        item = _find_by_id(target_menu.get("items", []), item_id)
        if item is None:
            raise ValueError(f"Menu item id {item_id} not found in '{position}'")
        item.update(fields)
        MenuItem.model_validate(item)
        self._stage_json(self.paths.config_dir / MENU_CONFIG, menus)
        self.log.info("Staged menu update of id %s in '%s': %s", item_id, position, sorted(fields))


def _attach_to_parent(items: list[dict], parent_id: int, new_item: dict) -> bool:
    for it in items:
        if it.get("id") == parent_id:
            it.setdefault("items", []).append(new_item)
            return True
        if _attach_to_parent(it.get("items", []), parent_id, new_item):
            return True
    return False


def _find_by_id(items: list[dict], item_id: int) -> dict | None:
    for it in items:
        if it.get("id") == item_id:
            return it
        found = _find_by_id(it.get("items", []), item_id)
        if found is not None:
            return found
    return None


def _remove_by_id(items: list[dict], item_id: int) -> bool:
    for idx, it in enumerate(items):
        if it.get("id") == item_id:
            items.pop(idx)
            return True
        if _remove_by_id(it.get("items", []), item_id):
            return True
    return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="publii_repository",
                                description="Read / inspect / mutate a Publii site.")
    p.add_argument("--site", required=True, help="Path to the Publii site root folder")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list-posts", help="List posts and pages")
    sp.add_argument("--type", choices=["all", "pages", "posts"], default="all")

    sub.add_parser("list-tags", help="List tags")
    sub.add_parser("integrity", help="Run relational integrity checks (read-only)")

    sp = sub.add_parser("show-post", help="Show one post with meta and tags")
    sp.add_argument("--id", type=int, required=True)

    sp = sub.add_parser("update-meta", help="Update _core meta for a post")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--meta-title")
    sp.add_argument("--meta-desc")

    sp = sub.add_parser("inject-menu", help="Inject a menu item")
    sp.add_argument("--position", required=True)
    sp.add_argument("--label", required=True)
    sp.add_argument("--type", default="page", choices=["page", "post", "external"])
    sp.add_argument("--link", required=True, help="post/page id (int) or URL/'#'")
    sp.add_argument("--parent-id", type=int, default=None)
    return p


def _cli(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )
    repo = PubliiRepository.open(args.site)

    if args.command == "list-posts":
        flag = {"all": None, "pages": True, "posts": False}[args.type]
        for post in repo.list_posts(pages=flag):
            kind = "PAGE" if post.is_page else "POST"
            print(f"{post.id:>3} [{kind}] {post.slug}  | {post.title}")
        return 0

    if args.command == "list-tags":
        for tag in repo.list_tags():
            print(f"{tag.id:>3}  {tag.slug:<32} {tag.name}")
        return 0

    if args.command == "integrity":
        issues = repo.check_integrity()
        if not issues:
            print("OK - no integrity issues.")
            return 0
        for i in issues:
            print(f"[{i.severity}] {i.code}: {i.detail}")
        return 1 if any(i.severity == "ERROR" for i in issues) else 0

    if args.command == "show-post":
        post = repo.get_post(args.id)
        if not post:
            print(f"No post with id {args.id}")
            return 1
        meta = repo.get_additional_data(args.id)
        print(json.dumps({
            "post": post.model_dump(),
            "core": meta.core.model_dump(exclude_none=True),
            "tag_ids": repo.get_post_tag_ids(args.id),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "update-meta":
        meta = repo.get_additional_data(args.id)
        if args.meta_title is not None:
            meta.core.metaTitle = args.meta_title
        if args.meta_desc is not None:
            meta.core.metaDesc = args.meta_desc
        with repo.transaction() as tx:
            tx.upsert_additional_data(args.id, meta)
        print(f"Updated meta for post {args.id}.")
        return 0

    if args.command == "inject-menu":
        try:
            link: str | int = int(args.link)
        except ValueError:
            link = args.link
        item = MenuItem(id=0, label=args.label, type=args.type, link=link)
        with repo.transaction() as tx:
            new_id = tx.inject_menu_item(args.position, item, parent_id=args.parent_id)
        print(f"Injected menu item id {new_id} into '{args.position}'.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
