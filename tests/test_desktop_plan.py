"""Selecting the right project when two of them share a display name.

Publii's site list shows the display name and nothing else. Two projects may
carry the same one -- the normal case for a bilingual site whose brand does not
translate. Selecting by name alone would publish whichever entry came first,
report success, and leave the other project unpublished.

These tests cover the part of that behaviour that is pure logic: which projects
get grouped, and when the module refuses to act. Everything downstream of the
grouping needs a running Publii and is verified by reading ``syncDate`` off the
disk at run time, not here.
"""

from __future__ import annotations

import json
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("The desktop driver is Windows only.", allow_module_level=True)

from publii_toolkit.desktop import DesktopError, PubliiDesktop  # noqa: E402


def make_sites(root, projects: dict[str, str]):
    """Create `directory -> displayName` sites under `root`, and open a driver."""
    for directory, display_name in projects.items():
        config = root / directory / "input" / "config"
        config.mkdir(parents=True)
        (config / "site.config.json").write_text(
            json.dumps({"name": directory, "displayName": display_name,
                        "syncDate": 1700000000000}),
            encoding="utf-8",
        )
    return PubliiDesktop(sites_root=root, executable=root / "Publii.exe")


BILINGUAL = {
    "netzero-fr": "Net Zero Technologies",
    "netzero-en": "Net Zero Technologies",
    "ssgi-fr": "Solutions Success",
    "ssgi-en": "Success Solutions",
}


def test_same_named_projects_are_grouped_together(tmp_path):
    driver = make_sites(tmp_path, BILINGUAL)
    groups = driver.directories_by_display_name()
    assert groups["Net Zero Technologies"] == ["netzero-en", "netzero-fr"]
    assert groups["Solutions Success"] == ["ssgi-fr"]


def test_a_unique_name_stays_its_own_group(tmp_path):
    driver = make_sites(tmp_path, BILINGUAL)
    plan = driver.plan(["ssgi-fr", "ssgi-en"])
    assert plan == [("Solutions Success", ["ssgi-fr"]),
                    ("Success Solutions", ["ssgi-en"])]


def test_requesting_one_of_two_same_named_projects_is_refused(tmp_path):
    """The refusal is the point: the alternative is publishing the wrong site."""
    driver = make_sites(tmp_path, BILINGUAL)
    with pytest.raises(DesktopError) as excinfo:
        driver.plan(["netzero-fr"])
    message = str(excinfo.value)
    assert "netzero-en" in message
    assert "cannot be told apart" in message


def test_requesting_both_same_named_projects_yields_one_group(tmp_path):
    driver = make_sites(tmp_path, BILINGUAL)
    plan = driver.plan(["netzero-fr", "netzero-en"])
    assert plan == [("Net Zero Technologies", ["netzero-en", "netzero-fr"])]


def test_a_group_is_planned_once_however_it_is_requested(tmp_path):
    driver = make_sites(tmp_path, BILINGUAL)
    plan = driver.plan(["netzero-fr", "ssgi-fr", "netzero-en"])
    assert [name for name, _ in plan] == ["Net Zero Technologies", "Solutions Success"]


def test_an_unknown_project_is_named_in_the_error(tmp_path):
    driver = make_sites(tmp_path, BILINGUAL)
    with pytest.raises(DesktopError, match="veranda-fr"):
        driver.plan(["veranda-fr"])


def test_a_dry_run_reports_every_project_of_a_group(tmp_path):
    """A dry run must show both projects, or the operator cannot see the pairing."""
    driver = make_sites(tmp_path, BILINGUAL)
    results = driver.sync(["netzero-fr", "netzero-en"], apply=False)
    assert {r.site for r in results} == {"netzero-fr", "netzero-en"}
    assert all(r.applied is False and r.detail == "dry run" for r in results)
