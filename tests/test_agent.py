"""The properties that make this safe to hand to an agent.

Each test names a way a confused or hostile command could damage a site, and
asserts that it cannot.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from publii_toolkit.agent import AgentSession, CommandError, describe_operations


def posts(site_root) -> dict[int, tuple]:
    conn = sqlite3.connect(site_root / "input" / "db.sqlite")
    rows = {r[0]: r for r in conn.execute("SELECT id, title, text, status FROM posts")}
    conn.close()
    return rows


# ------------------------------------------------------------------- catalogue


def test_the_catalogue_lists_every_operation():
    ops = describe_operations()
    assert "list_posts" in ops["read"]
    assert "update_post" in ops["write"]
    # Every operation documents itself, so an agent can read the contract once.
    assert all(text for text in ops["read"].values())
    assert all(text for text in ops["write"].values())


# ------------------------------------------------------------------------ read


def test_read_lists_posts_and_pages(site_root):
    out = AgentSession(str(site_root)).read({"op": "list_posts"})
    assert out["ok"]
    slugs = {p["slug"] for p in out["result"]}
    assert slugs == {"home", "about", "first-post"}


def test_read_can_filter_to_pages(site_root):
    out = AgentSession(str(site_root)).read({"op": "list_posts", "pages": True})
    assert {p["slug"] for p in out["result"]} == {"home", "about"}


def test_get_post_returns_body_and_metadata(site_root):
    out = AgentSession(str(site_root)).read({"op": "get_post", "slug": "home"})
    assert out["result"]["title"] == "Home"
    assert out["result"]["meta"]["metaTitle"] == "Home"


def test_menus_are_returned_with_nesting_intact(site_root):
    out = AgentSession(str(site_root)).read({"op": "list_menus"})
    items = out["result"][0]["items"]
    assert items[1]["items"][0]["label"] == "About"


# --------------------------------------------------------------- bad commands


def test_an_unknown_op_is_refused(site_root):
    with pytest.raises(CommandError, match="Unknown op"):
        AgentSession(str(site_root)).read({"op": "drop_everything"})


def test_a_command_without_an_op_is_refused(site_root):
    with pytest.raises(CommandError):
        AgentSession(str(site_root)).read({"slug": "home"})


def test_a_write_cannot_be_smuggled_through_read(site_root):
    with pytest.raises(CommandError, match="plan"):
        AgentSession(str(site_root)).read({"op": "update_post", "slug": "home", "title": "X"})


def test_a_read_is_refused_inside_a_write_batch(site_root):
    with pytest.raises(CommandError, match="read"):
        AgentSession(str(site_root)).plan([{"op": "list_posts"}])


def test_an_unknown_slug_is_reported_by_plan_not_raised(site_root):
    """An agent gets structured feedback it can act on, not an exception."""
    out = AgentSession(str(site_root)).plan(
        [{"op": "update_post", "slug": "ghost", "title": "X"}]
    )
    assert not out["ok"]
    assert out["error"]["code"] == "plan_invalid"
    assert "No post or page" in out["result"]["steps"][0]["problem"]


def test_plan_reports_every_problem_at_once(site_root):
    """Fix-one-rerun cycles are expensive for an agent, so report them all."""
    out = AgentSession(str(site_root)).plan(
        [
            {"op": "update_post", "slug": "ghost", "title": "X"},
            {"op": "update_post", "slug": "about", "title": "Fine"},
            {"op": "remove_menu_item", "position": "mainMenu"},
        ]
    )
    assert not out["ok"]
    assert len(out["result"]["problems"]) == 2
    assert "problem" not in out["result"]["steps"][1]


def test_update_post_with_no_fields_is_refused(site_root):
    session = AgentSession(str(site_root))
    out = session.apply([{"op": "update_post", "slug": "home"}])
    assert not out["ok"]
    assert out["error"]["code"] == "bad_command"


# ------------------------------------------------------------------- planning


def test_plan_writes_nothing(site_root):
    before = posts(site_root)
    out = AgentSession(str(site_root)).plan(
        [{"op": "update_post", "slug": "home", "title": "Changed"}]
    )
    assert out["ok"]
    assert posts(site_root) == before


def test_plan_describes_each_step_in_words(site_root):
    out = AgentSession(str(site_root)).plan(
        [{"op": "update_post", "slug": "about", "title": "About us"}]
    )
    assert "about" in out["result"]["steps"][0]["describes"]


# -------------------------------------------------------------------- applying


def test_apply_updates_a_title(site_root):
    session = AgentSession(str(site_root))
    out = session.apply([{"op": "update_post", "slug": "about", "title": "About us"}])
    assert out["ok"], out
    assert posts(site_root)[2][1] == "About us"


def test_apply_updates_metadata_and_keeps_unmodelled_keys(site_root):
    session = AgentSession(str(site_root))
    out = session.apply(
        [{"op": "update_meta", "slug": "home", "metaDesc": "A welcoming page"}]
    )
    assert out["ok"], out

    conn = sqlite3.connect(site_root / "input" / "db.sqlite")
    raw = conn.execute(
        "SELECT value FROM posts_additional_data WHERE post_id=1 AND key='_core'"
    ).fetchone()[0]
    conn.close()

    core = json.loads(raw)
    assert core["metaDesc"] == "A welcoming page"
    assert core["metaTitle"] == "Home"  # untouched
    assert core["editor"] == "markdown"  # a key the toolkit does not model


def test_a_batch_is_all_or_nothing(site_root):
    """The second command is invalid, so the first must not survive either."""
    session = AgentSession(str(site_root))
    before = posts(site_root)

    out = session.apply(
        [
            {"op": "update_post", "slug": "about", "title": "Should not persist"},
            {"op": "update_post", "slug": "home"},  # no fields, raises
        ]
    )

    assert not out["ok"]
    assert posts(site_root)[2][1] == before[2][1] == "About"


def test_a_backup_is_written_before_any_change(site_root):
    session = AgentSession(str(site_root))
    session.apply([{"op": "update_post", "slug": "about", "title": "About us"}])

    backups = list((site_root / "input_backup").glob("*/db.sqlite"))
    assert backups, "expected a backup of db.sqlite"

    conn = sqlite3.connect(backups[0])
    title = conn.execute("SELECT title FROM posts WHERE id=2").fetchone()[0]
    conn.close()
    assert title == "About", "the backup must hold the pre-change state"


def test_two_changes_in_the_same_second_keep_two_backups(site_root, monkeypatch):
    # The folder name has one-second resolution. Two back-to-back passes used to
    # share it, and the second backup silently replaced the first.
    from datetime import datetime as real_datetime

    import publii_toolkit.repository as repository

    class FrozenClock:
        @staticmethod
        def now(tz=None):
            return real_datetime(2026, 10, 8, 13, 0, 9, tzinfo=tz)

    monkeypatch.setattr(repository, "datetime", FrozenClock)
    session = AgentSession(str(site_root))
    session.apply([{"op": "update_post", "slug": "about", "title": "About us"}])
    session.apply([{"op": "update_post", "slug": "about", "title": "About the team"}])

    backups = sorted((site_root / "input_backup").glob("*/db.sqlite"))
    assert len(backups) == 2, "each change must keep its own backup"
    titles = []
    for path in backups:
        conn = sqlite3.connect(path)
        titles.append(conn.execute("SELECT title FROM posts WHERE id=2").fetchone()[0])
        conn.close()
    assert sorted(titles) == ["About", "About us"]


def test_menu_items_can_be_added_and_removed(site_root):
    session = AgentSession(str(site_root))

    added = session.apply(
        [{"op": "add_menu_item", "position": "mainMenu", "label": "Blog", "link": 3}]
    )
    assert added["ok"], added
    new_id = added["result"]["applied"][0]["result"]["id"]

    menus = session.read({"op": "list_menus"})["result"]
    assert any(i["label"] == "Blog" for i in menus[0]["items"])

    removed = session.apply(
        [{"op": "remove_menu_item", "position": "mainMenu", "id": new_id}]
    )
    assert removed["ok"], removed

    menus = AgentSession(str(site_root)).read({"op": "list_menus"})["result"]
    assert not any(i["label"] == "Blog" for i in menus[0]["items"])


def test_a_menu_item_is_renamed_in_place(site_root):
    # Remove-then-add would move the entry to the end of its level; a rename must not.
    session = AgentSession(str(site_root))
    before = session.read({"op": "list_menus"})["result"][0]["items"]

    preview = session.plan([{"op": "update_menu_item", "position": "mainMenu", "id": 3, "label": "About us"}])
    assert preview["ok"], preview
    assert "'About' -> 'About us'" in preview["result"]["steps"][0]["describes"]

    out = session.apply([{"op": "update_menu_item", "position": "mainMenu", "id": 3, "label": "About us"}])
    assert out["ok"], out

    after = AgentSession(str(site_root)).read({"op": "list_menus"})["result"][0]["items"]
    assert [i["id"] for i in after] == [i["id"] for i in before]
    nested = after[1]["items"][0]
    assert (nested["id"], nested["label"], nested["link"]) == (3, "About us", 2)


def test_a_menu_rename_cannot_retarget_the_entry(site_root):
    session = AgentSession(str(site_root))
    for bad in (
        {"op": "update_menu_item", "position": "mainMenu", "id": 3, "link": 1},
        {"op": "update_menu_item", "position": "mainMenu", "id": 3},
        {"op": "update_menu_item", "position": "mainMenu", "id": 99, "label": "Ghost"},
    ):
        assert not session.plan([bad])["ok"], bad
    menus = AgentSession(str(site_root)).read({"op": "list_menus"})["result"][0]["items"]
    assert menus[1]["items"][0]["label"] == "About"


def test_tags_can_be_replaced_by_slug(site_root):
    session = AgentSession(str(site_root))
    out = session.apply([{"op": "set_tags", "slug": "first-post", "tags": ["guides"]}])
    assert out["ok"], out

    tags = session.read({"op": "get_post", "slug": "first-post"})["result"]["tags"]
    assert tags == [2]


def test_set_tags_rejects_a_non_list(site_root):
    out = AgentSession(str(site_root)).apply(
        [{"op": "set_tags", "slug": "first-post", "tags": "guides"}]
    )
    assert not out["ok"]


# ------------------------------------------------------------------- integrity


def test_check_passes_on_a_healthy_site(site_root):
    out = AgentSession(str(site_root)).read({"op": "check"})
    assert out["result"]["ok"], out["result"]["issues"]
