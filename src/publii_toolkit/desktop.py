"""Drive the Publii desktop application to render and sync a site.

Publii has no CLI. Rendering and syncing are interface actions, which leaves a
manual click in the middle of an otherwise scriptable pipeline: everything from
writing content to committing it to the database can be automated, and then a
human has to press a button. This module closes that gap on Windows.

How it works
------------
Publii is an Electron application. Chromium only builds its accessibility tree
when something asks for it. Launched normally, Publii exposes nine empty panes
to Windows UI Automation and not a single button. Launched with
``--force-renderer-accessibility`` it exposes the whole interface by name,
including the site switcher and the "Sync your website" link. That flag is what
makes this module possible, and it is why the module always launches Publii
itself instead of attaching to a window that may have started without it.

Success is never inferred from the click. Publii writes ``syncDate`` into
``input/config/site.config.json`` when a sync completes, so this module reads
that value before and after and only reports success when it changes.

Safety
------
A sync publishes to the live site. The transaction backups taken elsewhere in
this toolkit protect ``input/``; they do not undo a publication. Hence:

- ``apply`` defaults to False, matching the rest of the toolkit;
- the module refuses to run while Publii is already open, because it cannot
  know whether that instance has the accessibility flag;
- it refuses to click any element whose label contains "delete": the site list
  places a "Delete website" link beside every entry;
- it re-reads the selected site name and compares it to the requested one
  immediately before clicking sync.

Requires the optional ``desktop`` extra::

    pip install "publii-agent-toolkit[desktop]"
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ACCESSIBILITY_FLAG = "--force-renderer-accessibility"
SYNC_LINK_TEXT = "Sync your website"
SYNC_MODAL_TITLE = "Website synchronisation"
FORBIDDEN_CLICK_TEXTS = ("delete", "supprimer")

TREE_READY_MIN_ELEMENTS = 40
TREE_TIMEOUT_S = 60
MODAL_TIMEOUT_S = 30
SYNC_TIMEOUT_S = 600

DEFAULT_EXECUTABLES = (
    r"%LOCALAPPDATA%\Programs\Publii\Publii.exe",
    r"%PROGRAMFILES%\Publii\Publii.exe",
    r"%PROGRAMFILES(X86)%\Publii\Publii.exe",
)


class DesktopError(RuntimeError):
    """The desktop application could not be driven to a verified outcome."""


@dataclass(frozen=True)
class SiteState:
    """What the toolkit knows about one site without opening Publii."""

    directory: str
    display_name: str
    sync_date_ms: int | None

    @property
    def last_sync(self) -> str:
        if not self.sync_date_ms:
            return "never"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.sync_date_ms / 1000))


@dataclass(frozen=True)
class SyncResult:
    site: str
    applied: bool
    succeeded: bool
    previous_sync_ms: int | None
    new_sync_ms: int | None
    detail: str = ""


def default_sites_root() -> Path:
    return Path.home() / "Documents" / "Publii" / "sites"


def find_executable() -> Path:
    import os

    for template in DEFAULT_EXECUTABLES:
        candidate = Path(os.path.expandvars(template))
        if candidate.exists():
            return candidate
    raise DesktopError(
        "Publii.exe was not found in the usual install locations. "
        "Pass executable=... with its full path."
    )


class PubliiDesktop:
    """Render and sync Publii sites by driving the desktop application."""

    def __init__(self, sites_root: str | Path | None = None,
                 executable: str | Path | None = None) -> None:
        if sys.platform != "win32":
            raise DesktopError("The desktop driver is Windows only.")
        self.sites_root = Path(sites_root) if sites_root else default_sites_root()
        if not self.sites_root.is_dir():
            raise DesktopError(f"Sites directory not found: {self.sites_root}")
        self._executable = Path(executable) if executable else None
        self._window = None

    # ── Disk, the source of truth ─────────────────────────────────

    @property
    def executable(self) -> Path:
        if self._executable is None:
            self._executable = find_executable()
        return self._executable

    def _config_path(self, site: str) -> Path:
        return self.sites_root / site / "input" / "config" / "site.config.json"

    def read_config(self, site: str) -> dict:
        path = self._config_path(site)
        if not path.exists():
            raise DesktopError(f"Site not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def state(self, site: str) -> SiteState:
        config = self.read_config(site)
        raw = config.get("syncDate")
        return SiteState(
            directory=site,
            display_name=config.get("displayName") or site,
            sync_date_ms=int(raw) if raw else None,
        )

    def sites(self) -> list[SiteState]:
        found = []
        for path in sorted(self.sites_root.iterdir()):
            if (path / "input" / "config" / "site.config.json").exists():
                try:
                    found.append(self.state(path.name))
                except (DesktopError, ValueError):
                    continue
        return found

    def display_names(self) -> dict[str, str]:
        return {state.display_name: state.directory for state in self.sites()}

    # ── Application lifecycle ─────────────────────────────────────

    @staticmethod
    def is_running() -> bool:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Publii.exe", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return "Publii.exe" in (result.stdout or "")

    @staticmethod
    def close() -> None:
        subprocess.run(["taskkill", "/IM", "Publii.exe", "/F"],
                       capture_output=True, check=False)

    def launch(self) -> None:
        subprocess.Popen(
            [str(self.executable), ACCESSIBILITY_FLAG],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _find_window(self, timeout_s: int = TREE_TIMEOUT_S):
        from pywinauto import Desktop

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for window in Desktop(backend="uia").windows():
                try:
                    if "publii" in (window.window_text() or "").lower():
                        return window
                except Exception:
                    continue
            time.sleep(1.5)
        raise DesktopError("The Publii window never appeared.")

    def _wait_for_tree(self, window, timeout_s: int = TREE_TIMEOUT_S) -> None:
        deadline = time.time() + timeout_s
        count = 0
        while time.time() < deadline:
            try:
                count = len(window.descendants())
            except Exception:
                count = 0
            if count >= TREE_READY_MIN_ELEMENTS:
                return
            time.sleep(2)
        raise DesktopError(
            f"The accessibility tree stayed empty ({count} elements). "
            f"Was Publii started with {ACCESSIBILITY_FLAG}?"
        )

    # ── Element handling ──────────────────────────────────────────

    @staticmethod
    def _text(element) -> str:
        try:
            return (element.window_text() or "").strip()
        except Exception:
            return ""

    @classmethod
    def _find(cls, children, text: str, control_types=None, exact: bool = False):
        for element in children:
            value = cls._text(element)
            if not value:
                continue
            if (value == text) if exact else (text.lower() in value.lower()):
                if control_types:
                    try:
                        if element.element_info.control_type not in control_types:
                            continue
                    except Exception:
                        continue
                return element
        return None

    @classmethod
    def _safe_click(cls, element, expected_text: str) -> None:
        actual = cls._text(element)
        lowered = actual.lower()
        for forbidden in FORBIDDEN_CLICK_TEXTS:
            if forbidden in lowered:
                raise DesktopError(f"Refused to click a destructive element: {actual!r}")
        if expected_text.lower() not in lowered:
            raise DesktopError(f"Refused to click: expected {expected_text!r}, found {actual!r}")
        element.click_input()

    def _current_site_display(self, window) -> str | None:
        known = set(self.display_names())
        for element in window.descendants():
            try:
                if element.element_info.control_type != "Text":
                    continue
            except Exception:
                continue
            text = self._text(element)
            if text in known:
                return text
        return None

    def _select_site(self, window, display_name: str) -> None:
        window.set_focus()
        time.sleep(1)

        current = self._current_site_display(window)
        if current == display_name:
            return
        if current is None:
            raise DesktopError("Could not read which site Publii currently has open.")

        header = self._find(window.descendants(), current, control_types=("Text",), exact=True)
        if header is None:
            raise DesktopError(f"Header for site {current!r} not found.")
        header.click_input()
        time.sleep(2.5)

        entry = self._find(window.descendants(), display_name, control_types=("ListItem",))
        if entry is None:
            raise DesktopError(f"Site {display_name!r} is not in the Publii list.")
        self._safe_click(entry, display_name)
        time.sleep(4)

        if self._current_site_display(window) != display_name:
            raise DesktopError(f"Selecting {display_name!r} did not take effect.")

    def _click_sync(self, window) -> None:
        """The sidebar link opens a modal; the modal's button starts the sync.

        Both carry the exact same label. Only the control type separates them:
        Hyperlink in the sidebar, Text in the modal. Clicking the sidebar twice
        reopens the modal and publishes nothing, while looking like a click that
        worked.
        """
        window.set_focus()
        time.sleep(1)

        link = self._find(window.descendants(), SYNC_LINK_TEXT,
                          control_types=("Hyperlink",), exact=True)
        if link is None:
            raise DesktopError(f"Sidebar link {SYNC_LINK_TEXT!r} not found.")
        self._safe_click(link, SYNC_LINK_TEXT)

        deadline = time.time() + MODAL_TIMEOUT_S
        while time.time() < deadline:
            time.sleep(1.5)
            children = window.descendants()
            if self._find(children, SYNC_MODAL_TITLE, control_types=("Text",), exact=True) is None:
                continue
            confirm = self._find(children, SYNC_LINK_TEXT, control_types=("Text",), exact=True)
            if confirm is not None:
                self._safe_click(confirm, SYNC_LINK_TEXT)
                return
        raise DesktopError(f"The {SYNC_MODAL_TITLE!r} modal never appeared.")

    def _wait_for_sync(self, site: str, previous: int | None,
                       timeout_s: int = SYNC_TIMEOUT_S) -> int | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            current = self.state(site).sync_date_ms
            if current and current != previous:
                return current
            time.sleep(3)
        return None

    # ── Public operation ──────────────────────────────────────────

    def sync(self, sites: list[str], apply: bool = False,
             keep_open: bool = False) -> list[SyncResult]:
        """Render and publish each site in order. Dry run unless apply is True."""
        states = [self.state(site) for site in sites]
        if not apply:
            return [
                SyncResult(site=s.directory, applied=False, succeeded=False,
                           previous_sync_ms=s.sync_date_ms, new_sync_ms=None,
                           detail="dry run")
                for s in states
            ]

        if self.is_running():
            raise DesktopError(
                "Publii is already open. Close it: this module must launch it with "
                f"{ACCESSIBILITY_FLAG}, otherwise no control is addressable."
            )

        self.launch()
        window = self._find_window()
        self._wait_for_tree(window)

        results = []
        try:
            for state in states:
                previous = state.sync_date_ms
                self._select_site(window, state.display_name)
                self._click_sync(window)
                new = self._wait_for_sync(state.directory, previous)
                results.append(SyncResult(
                    site=state.directory,
                    applied=True,
                    succeeded=new is not None,
                    previous_sync_ms=previous,
                    new_sync_ms=new,
                    detail="" if new else f"syncDate unchanged after {SYNC_TIMEOUT_S}s",
                ))
        finally:
            if not keep_open:
                self.close()
        return results
