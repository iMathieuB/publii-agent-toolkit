"""create_page, create_menu and same-batch references.

The use case: an agent builds a small site from nothing, five pages and their
menu, in one plan and one apply. Each test names what could go wrong on the way.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from publii_toolkit.agent import AgentSession, describe_operations
from publii_toolkit.repository import PubliiRepository

from conftest import build_site


def rows(site_root: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(site_root / "input" / "db.sqlite")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def pages_config(site_root: Path) -> list[dict]:
    return json.loads((site_root / "input" / "config" / "pages.config.json").read_text("utf-8"))


def page(slug: str, title: str = "T", text: str = "<p>x</p>", **kw) -> dict:
    return {"op": "create_page", "title": title, "slug": slug, "text": text, **kw}


@pytest.fixture
def fresh_site(tmp_path: Path) -> Path:
    """A site as Publii 0.47.6 creates it: no post, no page, no menu, no pages.config."""
    root = build_site(tmp_path / "fresh")
    conn = sqlite3.connect(root / "input" / "db.sqlite")
    for table in ("posts", "posts_additional_data", "posts_tags", "tags"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    config = root / "input" / "config"
    (config / "menu.config.json").write_text("[]", encoding="utf-8")
    (config / "pages.config.json").unlink()
    return root


# ------------------------------------------------------------------ catalogue


def test_the_catalogue_lists_the_new_commands():
    ops = describe_operations()
    for name in ("create_page", "create_menu"):
        assert name in ops["write"], name


# ----------------------------------------------------------------- happy path


def test_create_page_writes_the_row_meta_and_page_tree(site_root):
    session = AgentSession(str(site_root))
    out = session.apply([page("services", title="Services", text="<p>Ce que nous faisons</p>",
                              metaTitle="Nos services", metaDesc="Description.")])
    assert out["ok"], out
    created = out["result"]["applied"][0]["result"]
    new_id = created["id"]
    assert created["editor"] == "markdown"  # inferred: every row of the fixture is markdown

    (title, authors, slug, status, template, text), = rows(
        site_root, "SELECT title, authors, slug, status, template, text FROM posts WHERE id=?", (new_id,))
    assert (title, authors, slug, status, template) == ("Services", "1", "services", "published,is-page", "")
    assert text == "<p>Ce que nous faisons</p>"

    (core,), = rows(site_root, "SELECT value FROM posts_additional_data WHERE post_id=? AND key='_core'", (new_id,))
    # Exactly the five keys Publii writes for a page.
    assert json.loads(core) == {"metaTitle": "Nos services", "metaDesc": "Description.",
                                "metaRobots": "index, follow", "canonicalUrl": "", "editor": "markdown"}

    assert {"id": new_id, "subpages": []} in pages_config(site_root)
    assert out["result"]["backup"], "apply reports where the backup is"


def test_create_page_passes_the_integrity_check(site_root):
    session = AgentSession(str(site_root))
    assert session.apply([page("contact")])["ok"]
    check = AgentSession(str(site_root)).read({"op": "check"})["result"]
    assert check["ok"], check
    assert not check["issues"], check["issues"]


def test_a_subpage_is_registered_under_its_parent(site_root):
    session = AgentSession(str(site_root))
    out = session.apply([page("equipe", parent="about")])
    assert out["ok"], out
    about = next(e for e in pages_config(site_root) if e["id"] == 2)
    assert about["subpages"] == [{"id": out["result"]["applied"][0]["result"]["id"], "subpages": []}]
    assert AgentSession(str(site_root)).read({"op": "check"})["result"]["issues"] == []


def test_a_draft_page_keeps_the_draft_status(site_root):
    out = AgentSession(str(site_root)).apply([page("bientot", status="draft")])
    assert out["ok"], out
    assert rows(site_root, "SELECT status FROM posts WHERE slug='bientot'") == [("draft,is-page",)]


# ------------------------------------------------------------------- refusals


def test_a_duplicate_slug_is_refused_by_plan_and_by_apply(site_root):
    session = AgentSession(str(site_root))
    preview = session.plan([page("about")])
    assert not preview["ok"]
    assert "already used" in preview["result"]["steps"][0]["problem"]

    before = rows(site_root, "SELECT COUNT(*) FROM posts")
    out = session.apply([page("about")])
    assert not out["ok"] and out["error"]["code"] == "bad_command"
    assert rows(site_root, "SELECT COUNT(*) FROM posts") == before


def test_the_same_slug_twice_in_one_batch_is_refused(site_root):
    preview = AgentSession(str(site_root)).plan([page("offre"), page("offre")])
    assert not preview["ok"]
    assert "problem" not in preview["result"]["steps"][0]
    assert "already used" in preview["result"]["steps"][1]["problem"]


@pytest.mark.parametrize("bad", [
    page("A-Propos"),                       # upper case: Publii would rewrite it
    page("à-propos"),                       # accent
    page("a--propos"),                      # double hyphen
    page("media"),                          # reserved by Publii's URL scheme
    page("tags"),
    page("ok", status="published,featured"),
    page("ok", editor="blockeditor"),
    page("ok", metaTitle=12),
    page("ok", meta_title="typo"),          # unknown key: refused, not ignored
    page("ok", title="   "),
    {"op": "create_page", "slug": "ok", "text": "x"},
    page("ok", parent="ghost"),
    page("ok", parent="first-post"),        # a post cannot be a parent page
])
def test_malformed_create_page_commands_are_refused(site_root, bad):
    assert not AgentSession(str(site_root)).plan([bad])["ok"], bad


def test_wysiwyg_pages_need_html(site_root):
    out = AgentSession(str(site_root)).plan([page("ok", editor="tinymce", text="## Titre")])
    assert not out["ok"]
    assert "HTML" in out["result"]["problems"][0]


def test_plan_writes_nothing(site_root):
    db = site_root / "input" / "db.sqlite"
    config = site_root / "input" / "config"
    before = (db.read_bytes(), {p.name: p.read_bytes() for p in config.iterdir()})
    out = AgentSession(str(site_root)).plan([page("services"), page("contact")])
    assert out["ok"], out
    assert (db.read_bytes(), {p.name: p.read_bytes() for p in config.iterdir()}) == before
    assert not (site_root / "input_backup").exists()


def test_a_batch_with_one_bad_command_lands_nothing(site_root):
    before_posts = rows(site_root, "SELECT * FROM posts ORDER BY id")
    before_tree = pages_config(site_root)
    out = AgentSession(str(site_root)).apply([
        page("services"),
        page("contact"),
        page("about"),  # duplicate: the whole batch must fail
    ])
    assert not out["ok"]
    assert rows(site_root, "SELECT * FROM posts ORDER BY id") == before_posts
    assert pages_config(site_root) == before_tree
    assert not list((site_root / "input" / "config").glob("*.tmp"))


# ----------------------------------------------------------------- the editor


def test_a_fresh_site_needs_the_editor_spelled_out(fresh_site):
    session = AgentSession(str(fresh_site))
    out = session.plan([page("accueil")])
    assert not out["ok"]
    assert "editor" in out["result"]["problems"][0]

    out = session.apply([page("accueil", editor="tinymce")])
    assert out["ok"], out
    (core,), = rows(fresh_site, "SELECT value FROM posts_additional_data WHERE key='_core'")
    assert json.loads(core)["editor"] == "tinymce"


def test_a_site_mixing_editors_needs_the_editor_spelled_out(site_root):
    conn = sqlite3.connect(site_root / "input" / "db.sqlite")
    conn.execute("UPDATE posts_additional_data SET value=? WHERE post_id=3",
                 (json.dumps({"editor": "tinymce"}),))
    conn.commit()
    conn.close()
    out = AgentSession(str(site_root)).plan([page("ok")])
    assert not out["ok"]
    assert "markdown x2" in out["result"]["problems"][0]


# --------------------------------------------- a whole site, one plan, one apply


FIVE_PAGES = [
    page("accueil", title="Accueil", text="<p>Bienvenue</p>", editor="tinymce"),
    page("services", title="Services", text="<p>Nos services</p>", editor="tinymce"),
    page("a-propos", title="À propos", text="<p>Qui nous sommes</p>", editor="tinymce"),
    page("contact", title="Contact", text="<p>Écrivez-nous</p>", editor="tinymce"),
    page("politique-de-confidentialite", title="Politique de confidentialité",
         text="<p>Vos renseignements</p>", editor="tinymce"),
    {"op": "create_menu", "position": "mainMenu"},
    {"op": "add_menu_item", "position": "mainMenu", "label": "Accueil", "page": "accueil"},
    {"op": "add_menu_item", "position": "mainMenu", "label": "Services", "page": "services"},
    {"op": "add_menu_item", "position": "mainMenu", "label": "À propos", "page": "a-propos"},
    {"op": "add_menu_item", "position": "mainMenu", "label": "Contact", "page": "contact"},
    {"op": "create_menu", "position": "footerMenu", "name": "Pied de page"},
    {"op": "add_menu_item", "position": "footerMenu", "label": "Confidentialité",
     "page": "politique-de-confidentialite"},
]


def test_a_fresh_site_is_built_in_one_batch(fresh_site):
    session = AgentSession(str(fresh_site))
    preview = session.plan(FIVE_PAGES)
    assert preview["ok"], preview["result"]["problems"]

    out = session.apply(FIVE_PAGES)
    assert out["ok"], out

    ids = dict(rows(fresh_site, "SELECT slug, id FROM posts"))
    assert set(ids) == {"accueil", "services", "a-propos", "contact", "politique-de-confidentialite"}
    assert [e["id"] for e in pages_config(fresh_site)] == [ids[s] for s in
            ("accueil", "services", "a-propos", "contact", "politique-de-confidentialite")]

    menus = AgentSession(str(fresh_site)).read({"op": "list_menus"})["result"]
    main, footer = menus
    assert (main["position"], main["name"]) == ("mainMenu", "Main menu")
    assert [(i["label"], i["link"]) for i in main["items"]] == [
        ("Accueil", ids["accueil"]), ("Services", ids["services"]),
        ("À propos", ids["a-propos"]), ("Contact", ids["contact"])]
    all_ids = [i["id"] for m in menus for i in m["items"]]
    assert len(all_ids) == len(set(all_ids)), "menu item ids must be unique"
    assert footer["name"] == "Pied de page"

    raw = json.loads((fresh_site / "input" / "config" / "menu.config.json").read_text("utf-8"))
    assert raw[0]["maxLevels"] == "-1" and raw[1]["maxLevels"] == "1"
    assert AgentSession(str(fresh_site)).read({"op": "check"})["result"]["issues"] == []


def test_create_menu_refuses_a_position_the_theme_lacks_or_one_in_use(site_root):
    session = AgentSession(str(site_root))
    assert not session.plan([{"op": "create_menu", "position": "sidebar"}])["ok"]
    assert not session.plan([{"op": "create_menu", "position": "mainMenu"}])["ok"]  # fixture has it
    assert not session.plan([{"op": "create_menu", "position": "footerMenu"},
                             {"op": "create_menu", "position": "footerMenu"}])["ok"]


def test_a_menu_entry_needs_an_existing_menu(fresh_site):
    out = AgentSession(str(fresh_site)).plan([
        page("accueil", editor="markdown", text="Bienvenue"),
        {"op": "add_menu_item", "position": "mainMenu", "label": "Accueil", "page": "accueil"},
    ])
    assert not out["ok"]
    assert "create_menu" in out["result"]["problems"][0]


def test_two_menu_items_in_one_batch_get_distinct_ids(site_root):
    out = AgentSession(str(site_root)).apply([
        {"op": "add_menu_item", "position": "mainMenu", "label": "A", "link": 1},
        {"op": "add_menu_item", "position": "mainMenu", "label": "B", "link": 2},
    ])
    assert out["ok"], out
    a, b = (r["result"]["id"] for r in out["result"]["applied"])
    assert a != b


def test_update_meta_in_the_same_batch_keeps_the_new_pages_editor(fresh_site):
    out = AgentSession(str(fresh_site)).apply([
        page("accueil", editor="tinymce"),
        {"op": "update_meta", "slug": "accueil", "metaTitle": "Accueil"},
    ])
    assert out["ok"], out
    (core,), = rows(fresh_site, "SELECT value FROM posts_additional_data WHERE key='_core'")
    assert json.loads(core)["editor"] == "tinymce"
    assert json.loads(core)["metaTitle"] == "Accueil"


# ------------------------------------------------------------- Publii running


def test_apply_refuses_while_publii_is_open(site_root, monkeypatch):
    import publii_toolkit.agent as agent

    monkeypatch.setattr(agent, "publii_is_running", lambda: True)
    before = rows(site_root, "SELECT COUNT(*) FROM posts")
    out = AgentSession(str(site_root)).apply([page("services")])
    assert out["error"]["code"] == "publii_running"
    assert rows(site_root, "SELECT COUNT(*) FROM posts") == before


def test_unknown_tag_slug_is_a_bad_command_not_a_crash(site_root):
    out = AgentSession(str(site_root)).apply([{"op": "set_tags", "slug": "first-post", "tags": ["ghost"]}])
    assert out["error"]["code"] == "bad_command"


def test_integrity_reads_nested_pages(site_root):
    tree = [{"id": 1, "subpages": [{"id": 2, "subpages": []}]}]
    (site_root / "input" / "config" / "pages.config.json").write_text(json.dumps(tree), "utf-8")
    assert PubliiRepository.open(site_root).check_integrity() == []
