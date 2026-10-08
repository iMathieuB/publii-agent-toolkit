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

Sites that share a display name
-------------------------------
Nothing stops two Publii projects from carrying the same display name, and it
is the normal case for a bilingual site whose brand does not translate: the
French and English projects are both called "Net Zero Technologies". The site
list shows nothing but that name, so selecting by name alone is ambiguous - it
would publish whichever of the two comes first, look like it succeeded, and
leave the other behind.

This module therefore treats same-named projects as a group. It requires every
project sharing a name to be requested in the same call, walks the list entries
by position, and only works out which directory sat behind which position
afterwards, by reading which ``syncDate`` actually moved. No assumption is made
about the order Publii displays them in: the disk decides what was published,
as it does everywhere else in this module.

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
SYNC_DONE_TEXTS = (
    "Your website is now in sync",
    "All files have been successfully uploaded to your server.",
)
MODAL_ACK_TEXT = "OK"
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

    def directories_by_display_name(self) -> dict[str, list[str]]:
        """Display name -> every directory carrying it, sorted.

        A list, not a single directory: two projects may share a display name,
        and that is precisely the case that makes selection ambiguous.
        """
        groups: dict[str, list[str]] = {}
        for state in self.sites():
            groups.setdefault(state.display_name, []).append(state.directory)
        for directories in groups.values():
            directories.sort()
        return groups

    def display_names(self) -> dict[str, str]:
        """Display name -> one directory. Only used to test whether a name is known."""
        return {name: directories[0]
                for name, directories in self.directories_by_display_name().items()}

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
    def _find_all(cls, children, text: str, control_types=None, exact: bool = False):
        """Every match, in the order Windows exposes the tree."""
        found = []
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
                found.append(element)
        return found

    @classmethod
    def _find(cls, children, text: str, control_types=None, exact: bool = False):
        matches = cls._find_all(children, text, control_types, exact)
        return matches[0] if matches else None

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

    def _select_site(self, window, display_name: str, occurrence: int = 0,
                     expected_matches: int = 1) -> None:
        """Open the site called `display_name`, the `occurrence`-th of that name.

        The "already selected" shortcut only holds for a unique name. Once a
        name is carried by several projects it proves nothing, so the list has
        to be reopened and the requested position clicked even when the header
        already shows that name.
        """
        window.set_focus()
        time.sleep(1)

        current = self._current_site_display(window)
        if expected_matches == 1 and current == display_name:
            return
        if current is None:
            raise DesktopError("Could not read which site Publii currently has open.")

        header = self._find(window.descendants(), current, control_types=("Text",), exact=True)
        if header is None:
            raise DesktopError(f"Header for site {current!r} not found.")
        header.click_input()
        time.sleep(2.5)

        entries = self._find_all(window.descendants(), display_name,
                                 control_types=("ListItem",))
        if not entries:
            raise DesktopError(f"Site {display_name!r} is not in the Publii list.")
        if len(entries) != expected_matches:
            raise DesktopError(
                f"{len(entries)} entries carry {display_name!r} in the Publii list, "
                f"{expected_matches} expected from disk. Selection abandoned: "
                f"clicking at random would publish the wrong project."
            )

        self._safe_click(entries[occurrence], display_name)
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

    def _dismiss_sync_modal(self, window, timeout_s: int = 30) -> bool:
        """Close the "Your website is now in sync" modal.

        This modal covers the whole interface: while it is up, neither the site
        switcher nor the "Sync your website" link exists in the accessibility
        tree. Without this click the module can publish only one project per
        launch - the second fails with "site is not in the Publii list", which
        looks like a selection problem and is not one.

        Two conditions before clicking, because "OK" is too ordinary a label to
        trust on its own: either one of the known success-modal strings is
        present, or the sidebar sync link has vanished from the tree, which is
        proof that a modal is covering the interface. Otherwise nothing is
        clicked and the next step is left to fail loudly.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            children = window.descendants()
            ack = self._find(children, MODAL_ACK_TEXT,
                             control_types=("Text", "Button"), exact=True)
            if ack is not None:
                known = any(self._find(children, text, control_types=("Text",), exact=True)
                            for text in SYNC_DONE_TEXTS)
                covered = self._find(children, SYNC_LINK_TEXT,
                                     control_types=("Hyperlink",), exact=True) is None
                if known or covered:
                    self._safe_click(ack, MODAL_ACK_TEXT)
                    time.sleep(2)
                    return True
            time.sleep(1.5)
        return False

    def _wait_for_sync(self, site: str, previous: int | None,
                       timeout_s: int = SYNC_TIMEOUT_S) -> int | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            current = self.state(site).sync_date_ms
            if current and current != previous:
                return current
            time.sleep(3)
        return None

    def _wait_for_any_sync(self, directories: list[str], before: dict,
                           timeout_s: int = SYNC_TIMEOUT_S):
        """Wait for one syncDate in the group to move, and say which one.

        This is what replaces any assumption about Publii's display order: the
        project behind a list position is not guessed, it is read off the disk
        once the publication has happened.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for directory in directories:
                current = self.state(directory).sync_date_ms
                if current and current != before.get(directory):
                    return directory, current
            time.sleep(3)
        return None, None

    # ── Public operation ──────────────────────────────────────────

    def plan(self, sites: list[str]) -> list[tuple[str, list[str]]]:
        """Group the requested projects by display name, in the requested order.

        A same-named group is done whole or not at all: syncing one of two
        identically named projects would mean clicking one of two identical
        entries with no way to tell which, and so possibly publishing the other
        one without being asked to. The module refuses rather than gamble.
        """
        groups = self.directories_by_display_name()
        display_of = {directory: name
                      for name, directories in groups.items()
                      for directory in directories}

        ordered: list[tuple[str, list[str]]] = []
        seen: set[str] = set()
        for site in sites:
            if site in seen:
                continue
            name = display_of.get(site)
            if name is None:
                raise DesktopError(f"No such Publii project: {site}")
            siblings = groups[name]
            if len(siblings) > 1:
                missing = [s for s in siblings if s not in sites]
                if missing:
                    raise DesktopError(
                        f"{name!r} is carried by {len(siblings)} projects "
                        f"({', '.join(siblings)}). The Publii list shows nothing but "
                        f"that name, so these projects cannot be told apart at "
                        f"selection time. Request them all in the same call, or sync "
                        f"them by hand. Missing: {', '.join(missing)}."
                    )
            ordered.append((name, list(siblings)))
            seen.update(siblings)
        return ordered

    def _sync_group(self, window, display_name: str,
                    directories: list[str]) -> list[SyncResult]:
        """Sync every same-named project; report what each position published.

        The position clicked is never assumed to map to a directory: after each
        sync the group's syncDate values are read to see which one moved, and
        that reading is what counts. Publishing the same project twice costs
        nothing - the content being identical, Publii uploads no file - whereas
        a guess about display order would cost a project left behind.
        """
        total = len(directories)
        before = {d: self.state(d).sync_date_ms for d in directories}
        remaining = list(directories)
        results: list[SyncResult] = []
        occurrence = 0
        attempts = 0

        while remaining and attempts < 2 * total:
            self._select_site(window, display_name, occurrence=occurrence,
                              expected_matches=total)
            self._click_sync(window)
            moved, new = self._wait_for_any_sync(directories, before)
            self._dismiss_sync_modal(window)
            attempts += 1
            occurrence = (occurrence + 1) % total

            if moved is None:
                break
            before[moved] = new
            if moved in remaining:
                remaining.remove(moved)
                results.append(SyncResult(
                    site=moved, applied=True, succeeded=True,
                    previous_sync_ms=None, new_sync_ms=new,
                    detail=f"selected at list position {occurrence or total} of {total}",
                ))

        for directory in remaining:
            results.append(SyncResult(
                site=directory, applied=True, succeeded=False,
                previous_sync_ms=before[directory], new_sync_ms=None,
                detail=f"syncDate unchanged after {attempts} attempt(s) on "
                       f"{total} identically named entries",
            ))
        return results

    def sync(self, sites: list[str], apply: bool = False,
             keep_open: bool = False) -> list[SyncResult]:
        """Render and publish each site in order. Dry run unless apply is True."""
        grouped = self.plan(sites)
        if not apply:
            return [
                SyncResult(site=directory, applied=False, succeeded=False,
                           previous_sync_ms=self.state(directory).sync_date_ms,
                           new_sync_ms=None, detail="dry run")
                for _, directories in grouped
                for directory in directories
            ]

        if self.is_running():
            raise DesktopError(
                "Publii is already open. Close it: this module must launch it with "
                f"{ACCESSIBILITY_FLAG}, otherwise no control is addressable."
            )

        results = []
        try:
            # Launch and tree wait sit inside the try: if Publii starts but never
            # exposes its controls, it must still be closed, or the database stays
            # locked and the next run refuses to start.
            self.launch()
            window = self._find_window()
            self._wait_for_tree(window)

            for display_name, directories in grouped:
                if len(directories) > 1:
                    results.extend(self._sync_group(window, display_name, directories))
                    continue

                directory = directories[0]
                previous = self.state(directory).sync_date_ms
                self._select_site(window, display_name)
                self._click_sync(window)
                new = self._wait_for_sync(directory, previous)
                self._dismiss_sync_modal(window)
                results.append(SyncResult(
                    site=directory,
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
