"""Actuation through UI Automation patterns, and the Windows session lock.

Since 2026-11-12 the desktop driver sends no real mouse click: a sync must also
run while the Windows session is locked, where the lock screen keeps the
foreground. None of these tests launches Publii or publishes anything.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("The desktop driver is Windows only.", allow_module_level=True)

import publii_toolkit.desktop as desktop  # noqa: E402
from publii_toolkit.desktop import DesktopError, PubliiDesktop, SessionChanged  # noqa: E402


class El:
    def __init__(self, text, control_type, patterns=None, children=()):
        self.text = text
        self.patterns = patterns or {}
        self.children = list(children)
        self.element_info = SimpleNamespace(control_type=control_type)

    def window_text(self):
        return self.text

    def descendants(self, control_type=None):
        return [c for c in self.children
                if control_type in (None, c.element_info.control_type)]


@pytest.fixture
def driver(tmp_path, monkeypatch):
    for directory, name in {"fr": "Acme", "en": "Acme EN", "other": "Other"}.items():
        config = tmp_path / directory / "input" / "config"
        config.mkdir(parents=True)
        (config / "site.config.json").write_text(json.dumps({"displayName": name}), encoding="utf-8")
    monkeypatch.setattr(desktop, "session_locked", lambda: False)
    monkeypatch.setattr(desktop, "_get_pattern", lambda el, name: el.patterns.get(name))
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    d = PubliiDesktop(sites_root=tmp_path, executable=tmp_path / "Publii.exe")
    d._launch_lock_state = False
    return d


def invocable(text, control_type, calls):
    return El(text, control_type, {"Invoke": SimpleNamespace(Invoke=lambda: calls.append((text, control_type)))})


def test_the_lock_state_is_readable_on_this_machine():
    assert desktop.session_locked() in (True, False)


def test_invoke_is_used_and_no_mouse_is_needed(driver):
    calls = []
    assert driver._safe_invoke(invocable("Go back", "Button", calls), "Go back") == "Invoke"
    assert calls == [("Go back", "Button")]


def test_legacy_default_action_only_without_invoke(driver):
    calls = []
    el = El("Acme", "Text", {"LegacyIAccessible": SimpleNamespace(DoDefaultAction=lambda: calls.append("legacy"))})
    assert driver._safe_invoke(el, "Acme") == "LegacyIAccessible"
    assert calls == ["legacy"]


def test_no_fallback_when_invoke_fails(driver):
    calls = []

    def boom():
        raise RuntimeError("COM")
    el = El("Sync your website", "Button", {
        "Invoke": SimpleNamespace(Invoke=boom),
        "LegacyIAccessible": SimpleNamespace(DoDefaultAction=lambda: calls.append("legacy"))})
    with pytest.raises(DesktopError, match="Invoke failed"):
        driver._safe_invoke(el, "Sync your website")
    assert calls == []


def test_destructive_element_is_refused(driver):
    calls = []
    with pytest.raises(DesktopError, match="destructive"):
        driver._safe_invoke(invocable("Delete website", "Hyperlink", calls), "website")
    assert calls == []


def test_nothing_is_sent_after_the_session_changed(driver, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "session_locked", lambda: True)
    with pytest.raises(SessionChanged):
        driver._safe_invoke(invocable("Go back", "Button", calls), "Go back")
    assert calls == []


def test_entry_button_skips_other_options_and_longer_names(driver):
    entry = El("AcmeWebsite", "ListItem", children=[
        El("Other options: Acme", "Button"), El("Acme Website", "Button")])
    assert driver._entry_button(entry, "Acme").text == "Acme Website"
    longer = El("Acme EN", "ListItem", children=[El("Acme EN", "Button")])
    assert driver._entry_button(longer, "Acme", driver._longer_names("Acme")) is None
    active = El("Acme", "ListItem", children=[El("Other options: Acme", "Button")])
    assert driver._entry_button(active, "Acme") is None


def test_confirmation_refused_when_another_site_is_open(driver):
    calls = []
    window = El("Publii", "Window", children=[
        El("Other", "Button"),
        invocable("Sync your website", "Hyperlink", calls),
        El("Website synchronisation", "Text"),
        invocable("Sync your website", "Button", calls),
    ])
    with pytest.raises(DesktopError, match="Nothing was published"):
        driver._click_sync(window, "Acme")
    assert calls == [("Sync your website", "Hyperlink")]


def test_unreadable_lock_state_refuses_to_launch(driver, monkeypatch):
    monkeypatch.setattr(desktop, "session_locked", lambda: None)
    monkeypatch.setattr(PubliiDesktop, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(PubliiDesktop, "launch", lambda self: pytest.fail("must not launch"))
    with pytest.raises(DesktopError, match="cannot be read"):
        driver.sync(["fr"], apply=True)
