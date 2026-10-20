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


# ------------------------------------------------- a fresh, per-user install


def test_a_per_user_install_is_found_first(tmp_path, monkeypatch):
    """The Windows installer puts Publii under %LOCALAPPDATA%\Programs by default."""
    from publii_toolkit.desktop import find_executable

    exe = tmp_path / "Local" / "Programs" / "Publii" / "Publii.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "nowhere"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "nowhere"))
    assert find_executable() == exe


def test_no_install_is_a_clear_error(tmp_path, monkeypatch):
    from publii_toolkit.desktop import find_executable

    for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        monkeypatch.setenv(var, str(tmp_path / "nowhere"))
    with pytest.raises(DesktopError, match="executable="):
        find_executable()


def test_a_fresh_site_never_synced_is_planned_and_dry_run(tmp_path):
    """The site.config.json Publii writes for a new site: no syncDate, no advanced."""
    config = tmp_path / "mon-site" / "input" / "config"
    config.mkdir(parents=True)
    (config / "site.config.json").write_text(json.dumps({
        "uuid": "uuid-1", "name": "mon-site", "description": "", "displayName": "Mon site",
        "author": "", "logo": {"color": 1, "icon": "web-home"}, "theme": "simple",
    }), encoding="utf-8")
    driver = PubliiDesktop(sites_root=tmp_path, executable=tmp_path / "Publii.exe")
    assert driver.state("mon-site").last_sync == "never"
    assert driver.plan(["mon-site"]) == [("Mon site", ["mon-site"])]
    (result,) = driver.sync(["mon-site"], apply=False)
    assert (result.site, result.applied, result.previous_sync_ms) == ("mon-site", False, None)


def test_a_banner_publii_will_ask_to_review_is_refused_before_launch(tmp_path):
    """Publii would stop on its Privacy Settings dialog, which covers the sync button."""
    driver = make_sites(tmp_path, {"atelier": "Atelier"})
    path = tmp_path / "atelier" / "input" / "config" / "site.config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["advanced"] = {"gdpr": {"enabled": True, "settingsVersion": ""}}
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(DesktopError, match="Save settings"):
        driver.plan(["atelier"])

    config["advanced"]["gdpr"]["settingsVersion"] = "v2"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert driver.plan(["atelier"]) == [("Atelier", ["atelier"])]


class FakeElement:
    def __init__(self, text, control_type="ListItem"):
        self.text = text
        self.element_info = type("Info", (), {"control_type": control_type})()

    def window_text(self):
        return self.text


class FakeWindow:
    def __init__(self, elements):
        self.elements = elements

    def descendants(self):
        return self.elements


def test_the_site_list_does_not_mistake_a_longer_name_for_the_one_requested(tmp_path, monkeypatch):
    """Substring matching finds "Acme" inside "Acme EN"; that entry belongs to another project."""
    import publii_toolkit.desktop as desktop

    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    driver = make_sites(tmp_path, {"acme": "Acme", "acme-en": "Acme EN"})
    window = FakeWindow([FakeElement("Acme EN"), FakeElement("Acme"), FakeElement("Acme", "Text")])
    entries = driver._wait_for_entries(window, "Acme", timeout_s=1)
    assert [e.text for e in entries] == ["Acme"]


@pytest.mark.parametrize("header_type", ["Text", "Button"])
def test_the_open_site_is_read_from_the_header_in_either_publii_version(tmp_path, header_type):
    """Publii 0.47.6 shows the open site's name as a Text, 0.48.1 as a Button."""
    driver = make_sites(tmp_path, {"atelier": "Atelier Essai"})
    window = FakeWindow([FakeElement("Open application menu", "Button"),
                         FakeElement("Atelier Essai", header_type),
                         FakeElement("Pages", "Hyperlink")])
    assert driver._current_site_display(window) == "Atelier Essai"


class ClickableElement(FakeElement):
    def __init__(self, text, control_type, clicks):
        super().__init__(text, control_type)
        self.clicks = clicks

    def click_input(self):
        self.clicks.append((self.text, self.element_info.control_type))


class FocusWindow(FakeWindow):
    def set_focus(self):
        pass


@pytest.mark.parametrize("confirm_type", ["Text", "Button"])
def test_the_modal_button_is_clicked_never_the_sidebar_link_twice(tmp_path, monkeypatch, confirm_type):
    """0.47.6 exposes the modal's "Sync your website" as a Text, 0.48.1 as a Button."""
    import publii_toolkit.desktop as desktop

    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    driver = make_sites(tmp_path, {"atelier": "Atelier Essai"})
    clicks = []
    window = FocusWindow([
        ClickableElement("Sync your website", "Hyperlink", clicks),
        FakeElement("Website synchronisation", "Text"),
        ClickableElement("Sync your website", confirm_type, clicks),
    ])
    driver._click_sync(window)
    assert clicks == [("Sync your website", "Hyperlink"), ("Sync your website", confirm_type)]


@pytest.mark.parametrize("label", ["OK", "Close"])
def test_the_success_modal_is_closed_in_either_publii_version(tmp_path, monkeypatch, label):
    import publii_toolkit.desktop as desktop

    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    driver = make_sites(tmp_path, {"atelier": "Atelier Essai"})
    clicks = []
    window = FocusWindow([
        FakeElement("Your website is now in sync", "Text"),
        ClickableElement("Visit your website", "Button", clicks),
        ClickableElement(label, "Button", clicks),
    ])
    assert driver._dismiss_sync_modal(window, timeout_s=1) is True
    assert clicks == [(label, "Button")]


def test_a_lone_close_button_is_not_clicked(tmp_path, monkeypatch):
    """No success text and the sidebar link still visible: this Close is not the modal's."""
    import publii_toolkit.desktop as desktop

    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    driver = make_sites(tmp_path, {"atelier": "Atelier Essai"})
    clicks = []
    window = FocusWindow([
        ClickableElement("Sync your website", "Hyperlink", clicks),
        ClickableElement("Close", "Button", clicks),
    ])
    assert driver._dismiss_sync_modal(window, timeout_s=0.2) is False
    assert clicks == []


def test_a_name_in_some_other_control_is_not_taken_for_the_open_site(tmp_path):
    driver = make_sites(tmp_path, {"atelier": "Atelier Essai"})
    window = FakeWindow([FakeElement("Atelier Essai", "Hyperlink"), FakeElement("Pages", "Hyperlink")])
    assert driver._current_site_display(window) is None
