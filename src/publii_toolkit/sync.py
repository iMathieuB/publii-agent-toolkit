"""Stateless FR <-> EN diff engine for two Publii sites.

This module holds NO mutable instance state: every function takes the
repositories it needs. Diffing is pure-read. The only write path,
``apply_sync``, delegates ALL mutations to the *target* repository's
``PubliiTransaction`` context manager, inheriting its backup / atomicity /
rollback guarantees -- it never touches files or the DB directly.

v1 policy (confirmed):
  * diff + opt-in ``--apply``;
  * tags are REPORT-ONLY (never written) because tag ids diverge across the
    FR and EN sites (see design doc 1.5);
  * ``apply_sync`` performs only non-destructive STRUCTURAL parity actions
    (register a missing page in pages.config, inject a missing menu link). It
    never overwrites localized content (title/text/slug) -- doing so would
    clobber the human translation.

Requires: Python 3.10+, pydantic>=2.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .models import MenuDiff, MenuItem, PostDiff, SyncResult
from .repository import PubliiRepository, _iter_menu_items

LOGGER = logging.getLogger("publii.sync")

# Fields compared for drift. Localized fields (title/text/slug) are reported but
# never auto-written by apply_sync.
_DRIFT_FIELDS = ("status",)
_META_FIELDS = ("metaTitle", "metaDesc")

# ---------------------------------------------------------------------------
# Asymmetric tag-slug overrides (FR slug -> EN slug).
#
# These are EXPLICIT, human-confirmed mappings where the FR and EN tag slugs
# differ. Identity matches (slug == slug, e.g. "roi", "ai") are handled
# automatically and do NOT need an entry here.
#
# Rules:
#  * A value of None means "intentionally unmapped" (suppress auto-report).
#  * All entries are confirmed by Mathieu (Session A).
# ---------------------------------------------------------------------------
TAG_SLUG_OVERRIDES: dict[str, str | None] = {
    # FR "ia" (Intelligence Artificielle abbreviation) merges into EN "ai".
    # Confirmed: Session A decision.
    "ia": "ai",

    # FR "loi-25" -> EN "law-25". Tag exists on EN site.
    # Confirmed: Session A decision.
    "loi-25": "law-25",

    # FR "architecture-rag" -> EN "rag-architecture".
    # Confirmed: Session A decision.
    # NOTE: EN tag "rag-architecture" does not yet exist -- run
    # create_en_tag_rag_architecture.py --apply before any injection script.
    "architecture-rag": "rag-architecture",

    # FR "intelligence-artificielle" -> EN "artificial-intelligence".
    # EN tag exists. Confirmed via baseline + build_tag_map.py output.
    "intelligence-artificielle": "artificial-intelligence",

    # FR "automatisation-des-processus" -> EN "process-automation".
    # EN tag exists.
    "automatisation-des-processus": "process-automation",

    # FR "subventions-scale-ai" -> EN "scale-ai-grants".
    # EN tag exists.
    "subventions-scale-ai": "scale-ai-grants",

    # FR "productivite-manufacturiere" -> EN "manufacturing-productivity".
    # EN tag exists.
    "productivite-manufacturiere": "manufacturing-productivity",

    # FR "subventions" -> EN "grants".
    # EN tag exists.
    "subventions": "grants",

    # FR "pme-quebec" -> EN "quebec-smes" (existing EN tag).
    # Confirmed: Option A -- use existing EN tag; slug asymmetry acceptable.
    "pme-quebec": "quebec-smes",
}


def build_post_id_map(source: PubliiRepository, target: PubliiRepository) -> dict[int, int]:
    """Map source post id -> target post id.

    Page ids are aligned 1:1 across the sites (verified), so the primary key is
    the id itself; slug is a fallback for any id that does not exist on target.
    """
    src = source.list_posts()
    tgt = target.list_posts()
    tgt_ids = {p.id for p in tgt}
    tgt_by_slug = {p.slug: p.id for p in tgt}
    mapping: dict[int, int] = {}
    for p in src:
        if p.id in tgt_ids:
            mapping[p.id] = p.id
        elif p.slug in tgt_by_slug:
            mapping[p.id] = tgt_by_slug[p.slug]
    return mapping


def build_tag_slug_map(
    source: PubliiRepository,
    target: PubliiRepository,
    *,
    overrides: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    """Map source tag slug -> target tag slug.

    Resolution order:
      1. ``overrides`` dict (explicit human-confirmed asymmetric mappings).
         Pass None to suppress the report for a source slug.
      2. Identity match: source slug == target slug.
      3. None (unmapped) -- must be handled manually.

    Uses the module-level TAG_SLUG_OVERRIDES by default; pass an explicit
    overrides dict to replace it (useful for testing).
    """
    if overrides is None:
        overrides = TAG_SLUG_OVERRIDES
    tgt_slugs = {t.slug for t in target.list_tags()}
    result: dict[str, str | None] = {}
    for t in source.list_tags():
        s = t.slug
        if s in overrides:
            mapped = overrides[s]
            # Validate that the target slug actually exists (warn if not).
            if mapped is not None and mapped not in tgt_slugs:
                LOGGER.warning(
                    "TAG_SLUG_OVERRIDES: override '%s' -> '%s' but '%s' does "
                    "not exist on the target site -- create it before injecting.",
                    s, mapped, mapped,
                )
            result[s] = mapped
        else:
            result[s] = s if s in tgt_slugs else None
    return result


def diff_posts(source: PubliiRepository, target: PubliiRepository) -> list[PostDiff]:
    diffs: list[PostDiff] = []
    id_map = build_post_id_map(source, target)
    for sp in source.list_posts():
        tgt_id = id_map.get(sp.id)
        if tgt_id is None:
            diffs.append(PostDiff(kind="missing_on_target", source_id=sp.id,
                                  slug=sp.slug, note="No counterpart on target"))
            continue
        tp = target.get_post(tgt_id)
        drift = [f for f in _DRIFT_FIELDS if getattr(sp, f) != getattr(tp, f)]
        if drift:
            diffs.append(PostDiff(kind="field_drift", source_id=sp.id, target_id=tgt_id,
                                  slug=sp.slug, fields=drift))
        s_core = source.get_additional_data(sp.id).core
        t_core = target.get_additional_data(tgt_id).core
        meta_drift = [f for f in _META_FIELDS if getattr(s_core, f) != getattr(t_core, f)]
        if meta_drift:
            diffs.append(PostDiff(kind="meta_drift", source_id=sp.id, target_id=tgt_id,
                                  slug=sp.slug, fields=meta_drift,
                                  note="Localized meta differs (expected for FR/EN)"))
    return diffs


def diff_menus(source: PubliiRepository, target: PubliiRepository) -> list[MenuDiff]:
    diffs: list[MenuDiff] = []
    s_menus = {m.position: m for m in source.read_menus()}
    t_menus = {m.position: m for m in target.read_menus()}
    for pos, sm in s_menus.items():
        tm = t_menus.get(pos)
        if tm is None:
            diffs.append(MenuDiff(kind="missing_position", position=pos,
                                  detail="Menu position absent on target"))
            continue
        s_count = sum(1 for _ in _iter_menu_items(sm.items))
        t_count = sum(1 for _ in _iter_menu_items(tm.items))
        if s_count != t_count:
            diffs.append(MenuDiff(kind="item_count", position=pos,
                                  detail=f"source has {s_count} items, target has {t_count}"))
        # Compare the set of linked post/page ids (structure, not labels).
        s_links = {it.link for it in _iter_menu_items(sm.items)
                   if it.type in ("page", "post") and isinstance(it.link, int)}
        t_links = {it.link for it in _iter_menu_items(tm.items)
                   if it.type in ("page", "post") and isinstance(it.link, int)}
        only_src = s_links - t_links
        if only_src:
            diffs.append(MenuDiff(kind="label_path", position=pos,
                                  detail=f"linked ids on source but not target: {sorted(only_src)}"))
    return diffs


def build_report(source: PubliiRepository, target: PubliiRepository) -> str:
    lines = ["=" * 70, "PUBLII FR <-> EN DIFF REPORT", "=" * 70]
    lines.append(f"Source: {source.paths.root}")
    lines.append(f"Target: {target.paths.root}")

    lines.append("\n-- Post / page id map --")
    for s_id, t_id in sorted(build_post_id_map(source, target).items()):
        lines.append(f"  source {s_id} -> target {t_id}")

    lines.append("\n-- Tag slug map (REPORT-ONLY, never auto-written) --")
    for s_slug, t_slug in build_tag_slug_map(source, target).items():
        status = t_slug if t_slug else "UNMAPPED"
        lines.append(f"  {s_slug:<34} -> {status}")

    lines.append("\n-- Post diffs --")
    pdiffs = diff_posts(source, target)
    if not pdiffs:
        lines.append("  (none)")
    for d in pdiffs:
        lines.append(f"  [{d.kind}] {d.slug} fields={d.fields or '-'} {d.note}")

    lines.append("\n-- Menu diffs --")
    mdiffs = diff_menus(source, target)
    if not mdiffs:
        lines.append("  (none)")
    for d in mdiffs:
        lines.append(f"  [{d.kind}] {d.position}: {d.detail}")

    lines.append("=" * 70)
    return "\n".join(lines)


def apply_sync(source: PubliiRepository, target: PubliiRepository,
               *, apply: bool = False) -> SyncResult:
    """Apply non-destructive structural parity from source to target.

    With apply=False (default) this is a dry run: it returns the actions it
    WOULD take. With apply=True it opens target.transaction() and delegates
    every write to that Unit of Work.

    Scope (deliberately narrow for v1):
      * register on target any source-only is-page that already exists on target
        as a row but is missing from pages.config (structural parity);
      * never creates content, never copies title/text/slug, never writes tags.
    """
    result = SyncResult(applied=apply)

    # Menu: report ids linked on source but missing on target (NOT auto-injected
    # in v1 -- injecting requires a label, which is localized).
    for d in diff_menus(source, target):
        result.skipped.append(
            f"menu[{d.position}]: {d.detail} (manual: localized label needed)"
        )

    # Tags: report any slug that resolves to None after override lookup.
    for s_slug, t_slug in build_tag_slug_map(source, target).items():
        if t_slug is None:
            result.skipped.append(f"tag '{s_slug}': unmapped on target (manual)")

    # Posts: only act on a clearly-safe structural case -- a page row that exists
    # on the target but is not registered in its pages.config.
    target_pages = {p.id for p in target.list_posts(pages=True)}
    target_cfg_ids = {e.get("id") for e in target.read_pages_config()}
    to_register = sorted(target_pages - target_cfg_ids)

    for d in diff_posts(source, target):
        if d.kind == "missing_on_target":
            result.skipped.append(f"post '{d.slug}': missing on target (manual create)")
        elif d.kind == "field_drift":
            result.skipped.append(
                f"post '{d.slug}': status drift {d.fields} (manual review)"
            )
        elif d.kind == "meta_drift":
            result.skipped.append(
                f"post '{d.slug}': localized meta drift (expected, skipped)"
            )

    if not to_register:
        return result

    if not apply:
        result.post_actions = [
            f"would register page {pid} in pages.config" for pid in to_register
        ]
        return result

    with target.transaction() as tx:
        for pid in to_register:
            tx.register_page(pid)
            result.post_actions.append(f"registered page {pid} in pages.config")
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="publii_sync",
                                description="Diff (and optionally sync) two Publii sites.")
    p.add_argument("--source", required=True, help="Source site root (e.g. FR)")
    p.add_argument("--target", required=True, help="Target site root (e.g. EN)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("diff", help="Print a read-only diff report")
    sp = sub.add_parser("apply", help="Apply non-destructive structural parity")
    sp.add_argument("--apply", action="store_true",
                    help="Actually write (omit for dry-run)")
    return p


def _cli(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )
    source = PubliiRepository.open(args.source)
    target = PubliiRepository.open(args.target)

    if args.command == "diff":
        print(build_report(source, target))
        return 0

    if args.command == "apply":
        result = apply_sync(source, target, apply=args.apply)
        mode = "APPLIED" if result.applied else "DRY-RUN"
        print(f"== {mode} ==")
        for a in result.post_actions:
            print(f"  ACTION: {a}")
        for s in result.skipped:
            print(f"  SKIP:   {s}")
        if not result.post_actions:
            print("  (no auto-applicable actions)")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
