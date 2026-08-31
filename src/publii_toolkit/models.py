"""Pydantic data models for the Publii toolkit.

Validation is INHERENT to these models: parsing a SQLite row or a config-JSON
payload into one of these models enforces its structure on read. A malformed
row raises ``pydantic.ValidationError`` at the point of reading, which is why a
standalone validator module is not needed -- structural validation lives here,
relational integrity lives in ``publii_repository.PubliiRepository.check_integrity``.

All field names and JSON keys map ONLY to structures verified against the live
Publii sites (db.sqlite + input/config/*.json). No invented fields.

Requires: Python 3.10+, pydantic>=2.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SitePaths(BaseModel):
    """Resolved, validated paths for one Publii site.

    The source of truth for a Publii site is its ``input/`` directory. ``output/``
    is generated and must never be edited by this toolkit.
    """

    model_config = ConfigDict(frozen=True)

    root: Path
    input_dir: Path
    db_path: Path
    config_dir: Path
    media_dir: Path

    @classmethod
    def from_root(cls, site_root: str | Path) -> "SitePaths":
        """Build and validate paths from a site root folder.

        Raises:
            FileNotFoundError: if the site root, its ``input/`` dir, or
                ``input/db.sqlite`` do not exist.
        """
        root = Path(site_root).expanduser().resolve()
        input_dir = root / "input"
        db_path = input_dir / "db.sqlite"
        config_dir = input_dir / "config"
        media_dir = input_dir / "media"

        if not root.is_dir():
            raise FileNotFoundError(f"Site root not found: {root}")
        if not input_dir.is_dir():
            raise FileNotFoundError(f"Publii input dir not found: {input_dir}")
        if not db_path.is_file():
            raise FileNotFoundError(f"Publii database not found: {db_path}")

        return cls(
            root=root,
            input_dir=input_dir,
            db_path=db_path,
            config_dir=config_dir,
            media_dir=media_dir,
        )


class CoreMeta(BaseModel):
    """Payload of ``posts_additional_data`` row with key ``_core``.

    ``extra="allow"`` preserves any Publii key we did not model so it survives a
    read -> modify -> write round-trip untouched.
    """

    model_config = ConfigDict(extra="allow")

    metaTitle: str = ""
    metaDesc: str = ""
    metaRobots: str = "index, follow"
    canonicalUrl: str = ""
    editor: str = "markdown"
    # NOTE: Publii stores the main tag id INCONSISTENTLY -- usually a string
    # (e.g. "3") but sometimes an int (verified: EN post 10 = 9). Accept both and
    # preserve the original type on round-trip. Foreign reference into tags.id,
    # validated at the integrity stage, not here.
    mainTag: str | int | None = None


class ViewSettings(BaseModel):
    """Payload for ``postViewSettings`` / ``pageViewSettings`` keys.

    Opaque flag-bag; kept permissive to round-trip the nested Publii structure.
    """

    model_config = ConfigDict(extra="allow")


class PostAdditionalData(BaseModel):
    """Typed view over the EAV rows belonging to a single ``post_id``.

    Keys map to ``posts_additional_data.key`` values: ``_core`` (aliased to
    ``core``), ``postViewSettings``, ``pageViewSettings``.
    """

    model_config = ConfigDict(populate_by_name=True)

    core: CoreMeta = Field(default_factory=CoreMeta, alias="_core")
    postViewSettings: ViewSettings | None = None
    pageViewSettings: ViewSettings | None = None


class Post(BaseModel):
    """A row in the ``posts`` table. Pages and posts share this table; a row is a
    PAGE iff ``status`` contains ``is-page`` (verified)."""

    # extra="forbid" => an unexpected column (schema drift after a Publii upgrade)
    # raises on read instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    authors: str  # stored as a string id, e.g. "1"
    slug: str = Field(min_length=1)
    text: str
    featured_image_id: int | None = None
    created_at: int  # epoch milliseconds
    modified_at: int  # epoch milliseconds
    status: str
    template: str = ""

    @property
    def is_page(self) -> bool:
        return "is-page" in (self.status or "")

    @property
    def is_featured(self) -> bool:
        return "featured" in (self.status or "")


class Tag(BaseModel):
    """A row in the ``tags`` table. ``additional_data`` is a parsed JSON object."""

    id: int
    name: str
    slug: str = Field(min_length=1)
    description: str = ""
    additional_data: dict = Field(default_factory=dict)


class MenuItem(BaseModel):
    """A node in ``menu.config.json`` -> ``items`` (recursive).

    ``link`` is an INTEGER equal to ``posts.id`` when ``type`` is ``page``/``post``;
    it is the string ``"#"`` for container items (``type == "external"`` with
    children). ``extra="allow"`` preserves Publii keys we do not model.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    label: str
    type: str
    link: str | int
    title: str = ""
    target: str = "_self"
    rel: str = ""
    cssClass: str = ""
    isHidden: bool = False
    items: list["MenuItem"] = Field(default_factory=list)


class Menu(BaseModel):
    """A top-level menu object in ``menu.config.json``."""

    model_config = ConfigDict(extra="allow")

    name: str  # e.g. "Main Menu", "Footer Menu"
    position: str  # e.g. "mainMenu", "footerMenu"
    items: list[MenuItem] = Field(default_factory=list)


class IntegrityIssue(BaseModel):
    code: str
    severity: str  # "ERROR" | "WARN"
    detail: str


class PostDiff(BaseModel):
    kind: str  # "missing_on_target" | "field_drift" | "meta_drift"
    source_id: int
    target_id: int | None = None
    slug: str
    fields: list[str] = Field(default_factory=list)
    note: str = ""


class MenuDiff(BaseModel):
    kind: str  # "item_count" | "label_path" | "missing_position"
    position: str
    detail: str


class SyncResult(BaseModel):
    applied: bool
    post_actions: list[str] = Field(default_factory=list)
    menu_actions: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


# Resolve the recursive MenuItem forward reference.
MenuItem.model_rebuild()
