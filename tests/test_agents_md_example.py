"""The worked example in AGENTS.md runs as written.

An agent copies that example. If it stops working, the agent learns it from a
failed apply on someone's site; this test learns it first.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import sys
from pathlib import Path

from publii_toolkit import cli

from conftest import build_site
from test_settings_and_media import png_bytes

AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"


def example_batch() -> list[dict]:
    text = AGENTS_MD.read_text(encoding="utf-8")
    block = re.search(r"cat > site\.json <<'JSON'\n(.*?)\nJSON\n", text, re.S)
    assert block, "AGENTS.md lost its worked example"
    return json.loads(block.group(1))


def run_cli(args: list[str], stdin: str, monkeypatch, capsys) -> dict:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = cli.main(args)
    out = json.loads(capsys.readouterr().out)
    assert code == (0 if out["ok"] else 1)
    return out


def test_the_five_page_example_builds_a_fresh_site(tmp_path, monkeypatch, capsys):
    site = build_site(tmp_path / "atelier-dupont")
    conn = sqlite3.connect(site / "input" / "db.sqlite")
    for table in ("posts", "posts_additional_data", "posts_tags", "tags"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    (site / "input" / "config" / "menu.config.json").write_text("[]", encoding="utf-8")
    (site / "input" / "config" / "pages.config.json").unlink()

    logo = tmp_path / "logo-atelier.png"
    logo.write_bytes(png_bytes())
    batch = example_batch()
    for command in batch:
        if command["op"] == "add_media":
            command["file"] = str(logo)
    payload = json.dumps(batch)

    planned = run_cli(["--site", str(site), "plan"], payload, monkeypatch, capsys)
    assert planned["ok"], planned["result"]["problems"]
    applied = run_cli(["--site", str(site), "apply"], payload, monkeypatch, capsys)
    assert applied["ok"], applied
    assert applied["result"]["backup"]

    pages = run_cli(["--site", str(site), "read"], '{"op":"list_posts","pages":true}', monkeypatch, capsys)
    assert len(pages["result"]) == 5
    settings = run_cli(["--site", str(site), "read"], '{"op":"get_site_settings"}', monkeypatch, capsys)
    assert settings["result"]["site_name"] == "Atelier Dupont"
    assert settings["result"]["logo"] == "media/website/logo-atelier.png"
    assert settings["result"]["cookie_banner"] is True
    check = run_cli(["--site", str(site), "check"], "", monkeypatch, capsys)
    assert check["result"]["issues"] == []
