"""update_site_settings and add_media.

Both reach outside the database: settings live in JSON next to the deployment
credentials, and media are files the input/ backup does not cover. Each test
names a way that could go wrong.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zlib
from pathlib import Path

import pytest

from publii_toolkit.agent import AgentSession, describe_operations

SECRET = "AKIA-not-a-real-key-but-treat-it-as-one"


def png_bytes() -> bytes:
    """A real 1x1 PNG, so nothing here depends on an image library."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


@pytest.fixture
def images(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "incoming"
    folder.mkdir()
    files = {
        "logo.png": png_bytes(),
        "Photo équipe.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 64,
        "logo.svg": b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>',
        "evil.svg": b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        "notes.png": b"this is text pretending to be a picture",
        "tool.exe": b"MZ\x90\x00" + b"\x00" * 64,
        "empty.png": b"",
    }
    out = {}
    for name, data in files.items():
        (folder / name).write_bytes(data)
        out[name] = folder / name
    return out


@pytest.fixture
def configured_site(site_root: Path) -> Path:
    """The fixture site with a site.config.json shaped like a live one, secrets included."""
    config = site_root / "input" / "config"
    (config / "site.config.json").write_text(json.dumps({
        "uuid": "uuid-1", "name": "site", "displayName": "Test site", "description": "",
        "logo": {"color": 1, "icon": "web-home"},
        "advanced": {"metaDescription": "", "gdpr": {"enabled": False, "popupTitlePrimary": "Cookies"},
                     "customHeadCode": "<!-- keep me -->"},
        "deployment": {"protocol": "s3", "s3": {"id": SECRET, "key": SECRET, "bucket": "b"},
                       "password": SECRET},
        "theme": "simple", "syncDate": 1700000000000,
    }, indent=4), encoding="utf-8")
    return site_root


def site_config(root: Path) -> dict:
    return json.loads((root / "input" / "config" / "site.config.json").read_text("utf-8"))


def theme_config(root: Path) -> dict:
    return json.loads((root / "input" / "config" / "theme.config.json").read_text("utf-8"))


def test_the_catalogue_lists_settings_and_media():
    ops = describe_operations()
    assert {"update_site_settings", "add_media"} <= set(ops["write"])
    assert "get_site_settings" in ops["read"]


# ------------------------------------------------------------- site settings


def test_settings_round_trip_and_keep_every_other_key(configured_site):
    before = site_config(configured_site)
    out = AgentSession(str(configured_site)).apply([{
        "op": "update_site_settings", "site_name": "Atelier Dupont",
        "site_description": "Réparation de vélos à Québec.", "cookie_banner": True,
        "privacy_policy_page": "about", "front_page": "home",
    }])
    assert out["ok"], out

    after = site_config(configured_site)
    assert after["displayName"] == "Atelier Dupont"
    assert after["advanced"]["metaDescription"] == "Réparation de vélos à Québec."
    gdpr = after["advanced"]["gdpr"]
    assert (gdpr["enabled"], gdpr["privacyPolicyPostId"], gdpr["privacyPolicyLinkType"],
            gdpr["showPrivacyPolicyLink"]) == (True, 2, "internal", True)
    assert (after["advanced"]["usePageAsFrontpage"], after["advanced"]["pageAsFrontpage"]) == (True, 1)

    # Unmodelled keys survive untouched, credentials included.
    assert after["deployment"] == before["deployment"]
    assert after["advanced"]["customHeadCode"] == "<!-- keep me -->"
    assert gdpr["popupTitlePrimary"] == "Cookies"
    assert (after["uuid"], after["name"], after["logo"], after["syncDate"]) == (
        before["uuid"], before["name"], before["logo"], before["syncDate"])
    assert AgentSession(str(configured_site)).read({"op": "check"})["result"]["issues"] == []


@pytest.mark.parametrize("bad", [
    {"deployment": {"s3": {"key": "x"}}},
    {"displayName": "x"},                  # the raw key, not the whitelisted name
    {"site_name": "Ok", "customHeadCode": "<script>"},
    {"site_name": ""},
    {"cookie_banner": "yes"},
    {"privacy_policy_page": "first-post"},  # a post, not a page
    {"front_page": "ghost"},
    {"logo": "../../db.sqlite"},
    {"logo": "media/website/missing.png"},
    {},
])
def test_anything_outside_the_whitelist_is_refused(configured_site, bad):
    before = (configured_site / "input" / "config" / "site.config.json").read_bytes()
    session = AgentSession(str(configured_site))
    assert not session.plan([{"op": "update_site_settings", **bad}])["ok"], bad
    out = session.apply([{"op": "update_site_settings", **bad}])
    assert not out["ok"], bad
    assert (configured_site / "input" / "config" / "site.config.json").read_bytes() == before


def test_no_secret_ever_reaches_the_output(configured_site):
    session = AgentSession(str(configured_site))
    outputs = [
        session.read({"op": "get_site_settings"}),
        session.plan([{"op": "update_site_settings", "site_name": "X", "deployment": 1}]),
        session.plan([{"op": "update_site_settings", "site_name": "X"}]),
        session.apply([{"op": "update_site_settings", "site_name": "X"}]),
    ]
    for out in outputs:
        assert SECRET not in json.dumps(out), out


def test_get_site_settings_reports_menu_positions_and_editors(configured_site):
    out = AgentSession(str(configured_site)).read({"op": "get_site_settings"})["result"]
    assert out["site_name"] == "Test site"
    assert out["theme_menu_positions"]["mainMenu"]["in_use"] is True
    assert out["theme_menu_positions"]["footerMenu"]["in_use"] is False
    assert out["content_editors"] == {"markdown": 3}


def test_settings_can_point_at_a_page_created_in_the_same_batch(configured_site):
    out = AgentSession(str(configured_site)).apply([
        {"op": "create_page", "title": "Politique", "slug": "politique-de-confidentialite",
         "text": "Texte."},
        {"op": "update_site_settings", "cookie_banner": True,
         "privacy_policy_page": "politique-de-confidentialite"},
    ])
    assert out["ok"], out
    new_id = out["result"]["applied"][0]["result"]["id"]
    assert site_config(configured_site)["advanced"]["gdpr"]["privacyPolicyPostId"] == new_id


def test_a_fresh_site_config_gets_partial_objects(site_root):
    # A new Publii site has no "advanced" object at all; Publii deep-merges its
    # defaults when it reads the file, so a partial object is what it expects.
    out = AgentSession(str(site_root)).apply([{"op": "update_site_settings", "cookie_banner": True}])
    assert out["ok"], out
    assert site_config(site_root)["advanced"] == {"gdpr": {"enabled": True}}


# --------------------------------------------------------------------- media


def test_a_logo_is_copied_and_set_in_one_batch(configured_site, images):
    session = AgentSession(str(configured_site))
    batch = [
        {"op": "add_media", "file": str(images["logo.png"]), "target": "logo"},
        {"op": "update_site_settings", "logo": "media/website/logo.png"},
    ]
    assert session.plan(batch)["ok"], session.plan(batch)
    out = session.apply(batch)
    assert out["ok"], out
    assert out["result"]["applied"][0]["result"]["value"] == "media/website/logo.png"
    target = configured_site / "input" / "media" / "website" / "logo.png"
    assert target.read_bytes() == images["logo.png"].read_bytes()
    assert theme_config(configured_site) == {"config": {"logo": "media/website/logo.png"}}
    assert not list(target.parent.glob("*.tmp"))


def test_a_post_image_lands_in_the_posts_folder_and_can_be_featured(site_root, images):
    out = AgentSession(str(site_root)).apply([{
        "op": "add_media", "file": str(images["Photo équipe.jpg"]), "target": "post",
        "slug": "about", "featured": True, "alt": "L'équipe",
    }])
    assert out["ok"], out
    result = out["result"]["applied"][0]["result"]
    assert result["value"] == "#DOMAIN_NAME#Photo-equipe.jpg"
    assert (site_root / "input" / "media" / "posts" / "2" / "Photo-equipe.jpg").is_file()

    conn = sqlite3.connect(site_root / "input" / "db.sqlite")
    url, extra = conn.execute("SELECT url, additional_data FROM posts_images").fetchone()
    featured = conn.execute("SELECT featured_image_id FROM posts WHERE id=2").fetchone()[0]
    conn.close()
    assert url == "Photo-equipe.jpg" and json.loads(extra)["alt"] == "L'équipe"
    assert featured == result["featured_image_id"]


def test_an_svg_logo_is_accepted(site_root, images):
    out = AgentSession(str(site_root)).apply([{"op": "add_media", "file": str(images["logo.svg"]),
                                               "target": "logo"}])
    assert out["ok"], out


@pytest.mark.parametrize("name, extra", [
    ("notes.png", {}),                       # text with an image extension
    ("tool.exe", {}),                        # not an image at all
    ("empty.png", {}),
    ("evil.svg", {}),                        # SVG carrying an event handler
    ("logo.png", {"name": "../../../evil.png"}),
    ("logo.png", {"name": "..\\db.sqlite"}),
    ("logo.png", {"name": "C:evil.png"}),
    ("logo.png", {"name": "logo.jpg"}),      # extension changed
    ("logo.png", {"name": "logo.html"}),
    ("logo.png", {"slug": "about"}),         # target logo takes no post
    ("logo.png", {"target": "post"}),        # target post needs one
    ("logo.png", {"target": "favicon"}),
    ("logo.png", {"overwrite": True}),
])
def test_bad_media_is_refused(site_root, images, name, extra):
    args = {"op": "add_media", "file": str(images[name]), "target": "logo", **extra}
    session = AgentSession(str(site_root))
    assert not session.plan([args])["ok"], args
    assert not session.apply([args])["ok"], args
    media = site_root / "input" / "media"
    assert not [p for p in media.rglob("*") if p.is_file()], "nothing may be written"
    assert not (site_root / "evil.png").exists()


def test_a_missing_source_file_is_refused(site_root, tmp_path):
    out = AgentSession(str(site_root)).plan([{"op": "add_media", "file": str(tmp_path / "nope.png"),
                                              "target": "logo"}])
    assert not out["ok"]


def test_a_different_file_is_never_overwritten(site_root, images, tmp_path):
    session = AgentSession(str(site_root))
    assert session.apply([{"op": "add_media", "file": str(images["logo.png"]), "target": "logo"}])["ok"]
    other = tmp_path / "logo.png"
    other.write_bytes(png_bytes() + b"different")
    out = session.apply([{"op": "add_media", "file": str(other), "target": "logo"}])
    assert not out["ok"]
    assert "already on the site" in out["error"]["detail"]
    # The same bytes again are a no-op, not an error.
    again = session.apply([{"op": "add_media", "file": str(images["logo.png"]), "target": "logo"}])
    assert again["ok"] and again["result"]["applied"][0]["result"]["copied"] is False


def test_media_is_removed_when_the_batch_fails(site_root, images):
    out = AgentSession(str(site_root)).apply([
        {"op": "add_media", "file": str(images["logo.png"]), "target": "post", "slug": "first-post"},
        {"op": "add_media", "file": str(images["logo.png"]), "target": "logo"},
        {"op": "update_post", "slug": "ghost", "title": "fails"},
    ])
    assert not out["ok"]
    media = site_root / "input" / "media"
    assert not [p for p in media.rglob("*") if p.is_file()]
    assert not (media / "posts" / "3").exists(), "folders the batch created are removed too"


def test_media_for_a_page_created_in_the_same_batch(site_root, images):
    out = AgentSession(str(site_root)).apply([
        {"op": "create_page", "title": "Équipe", "slug": "equipe", "text": "Nous."},
        {"op": "add_media", "file": str(images["logo.png"]), "target": "post", "slug": "equipe",
         "name": "equipe.png", "featured": True},
    ])
    assert out["ok"], out
    new_id = out["result"]["applied"][0]["result"]["id"]
    assert (site_root / "input" / "media" / "posts" / str(new_id) / "equipe.png").is_file()
