"""A throwaway Publii site, structurally faithful to a real one."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

SCHEMA = """
CREATE TABLE posts (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    slug TEXT NOT NULL,
    text TEXT NOT NULL,
    featured_image_id INTEGER,
    created_at INTEGER NOT NULL,
    modified_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    template TEXT NOT NULL DEFAULT ''
);
CREATE TABLE posts_additional_data (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL
);
CREATE TABLE posts_tags (
    id INTEGER PRIMARY KEY,
    tag_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL
);
CREATE TABLE posts_images (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    additional_data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    additional_data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE authors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    additional_data TEXT NOT NULL DEFAULT '{}'
);
"""


def _core(**kw) -> str:
    base = {
        "metaTitle": "",
        "metaDesc": "",
        "metaRobots": "index, follow",
        "canonicalUrl": "",
        "editor": "markdown",
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def build_site(root: Path) -> Path:
    """Create a Publii site at ``root`` and return it."""
    input_dir = root / "input"
    config_dir = input_dir / "config"
    (input_dir / "media").mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(input_dir / "db.sqlite")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO posts VALUES (1,'Home','1','home','<p>Welcome</p>',NULL,0,0,'published,is-page','')"
    )
    conn.execute(
        "INSERT INTO posts VALUES (2,'About','1','about','<p>Our story</p>',NULL,0,0,'published,is-page','')"
    )
    conn.execute(
        "INSERT INTO posts VALUES (3,'First post','1','first-post','<p>Hello</p>',NULL,0,0,'published','')"
    )
    conn.execute("INSERT INTO posts_additional_data VALUES (1,1,'_core',?)", (_core(metaTitle="Home"),))
    conn.execute("INSERT INTO posts_additional_data VALUES (2,2,'_core',?)", (_core(metaTitle="About"),))
    conn.execute("INSERT INTO posts_additional_data VALUES (3,3,'_core',?)", (_core(),))
    conn.execute("INSERT INTO tags VALUES (1,'News','news','',?)", ("{}",))
    conn.execute("INSERT INTO tags VALUES (2,'Guides','guides','',?)", ("{}",))
    conn.execute("INSERT INTO posts_tags VALUES (1,1,3)")
    conn.execute("INSERT INTO authors VALUES (1,'Author','author','','{}','{}')")
    conn.commit()
    conn.close()

    (config_dir / "site.config.json").write_text(
        json.dumps({"site": {"displayName": "Test site"}}), encoding="utf-8"
    )
    (config_dir / "menu.config.json").write_text(
        json.dumps(
            [
                {
                    "name": "Main Menu",
                    "position": "mainMenu",
                    "items": [
                        {"id": 1, "label": "Home", "type": "page", "link": 1, "items": []},
                        {
                            "id": 2,
                            "label": "More",
                            "type": "external",
                            "link": "#",
                            "items": [
                                {"id": 3, "label": "About", "type": "page", "link": 2, "items": []}
                            ],
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    # Every is-page row must be listed here, or the integrity check warns.
    (config_dir / "pages.config.json").write_text(
        json.dumps([{"id": 1, "parent": None}, {"id": 2, "parent": None}]), encoding="utf-8"
    )
    return root


@pytest.fixture
def site_root(tmp_path: Path) -> Path:
    return build_site(tmp_path / "site")
