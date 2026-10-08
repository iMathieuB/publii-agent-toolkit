# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-10-08

Four defects, each found in production use. The first two were found by driving the desktop module against six real sites instead of two, and both made it report success while leaving a site unpublished, which is the one failure mode a publishing tool must not have. The third left Publii open and the database locked after a launch that never exposed its controls. The fourth was in the repository: two transactions in the same second shared one backup folder.

### Fixed

- **A sync could only ever publish one site per launch.** Publii raises a "Your website is now in sync" modal when a sync completes, and that modal covers the whole interface: while it is up, neither the site switcher nor the "Sync your website" link exists in the accessibility tree. The driver never dismissed it, so the second site in a batch failed with "site is not in the Publii list", a message that points at selection and hides the real cause. The driver now acknowledges the modal before moving on, and only clicks that "OK" when a known completion string is present or the sidebar link has disappeared, because "OK" alone is too ordinary a label to trust.

- **Two projects sharing a display name could not be told apart.** Nothing stops two Publii projects from carrying the same display name, and it is the normal case for a bilingual site whose brand does not translate. The site list shows nothing but that name, so selecting by name published whichever entry came first and reported success, leaving the other project behind and looking like it had worked, since the verification read the same name back.

- **A failed launch left Publii open.** `sync()` launched Publii and waited for its accessibility tree before entering the `try` whose `finally` closes it. When the tree never filled (seen with the screen locked during a remote session: Publii exposed 9 empty containers), `_wait_for_tree` raised and Publii stayed open, holding the database lock, so the next run refused to start until someone closed it by hand. Launch and tree wait now sit inside the `try`.

- **Two transactions in the same second shared one backup.** The backup folder under `input_backup/` is named by the UTC time to the second, and it was created with `exist_ok=True`. Two passes run back to back wrote into the same folder, and the second copy of `db.sqlite` replaced the first, so the state before the first pass was no longer on disk. A folder that already exists now gets a `-2`, `-3`… suffix instead of being reused.

- **The README told you to `pip install` a package that is not on PyPI.** It now installs from GitHub.

### Added

- `update_menu_item`, as a JSON command and as `PubliiTransaction.update_menu_item()`: renames a menu entry (its `label`, or its `title`) in place. Removing an entry and adding a copy moved it to the end of its level, which made a rename impossible without reordering the menu. The command refuses to change an entry's target: pointing it elsewhere stays a remove and an add, so the integrity check sees the new link.
- `PubliiDesktop.plan()` groups the requested projects by display name and **refuses** to act when only part of a same-named group is requested: syncing one of two identical entries means possibly publishing the other without being asked to.
- `PubliiDesktop.directories_by_display_name()` replaces the collapsing `display_names()` lookup, which silently lost one of any two projects sharing a name. `display_names()` remains for membership tests.
- Same-named groups are published by walking the list positions, and which directory sat behind which position is worked out **afterwards**, by reading which `syncDate` moved. No assumption is made about Publii's display order. Publishing one project twice costs nothing (the content being identical, Publii uploads no file), whereas a guess about ordering costs a project left unpublished.
- Tests: `tests/test_desktop_plan.py`, seven tests over the grouping and refusal logic; two tests for `update_menu_item` (a rename keeps the entry's position; a retarget, an empty update or an unknown id is refused); one test for distinct backups within the same second.

## [1.1.0] — 2026-09-07

### Added

- **`publii_toolkit.desktop`** — drives the Publii desktop application to render
  and publish a site, closing the last manual step in an otherwise scriptable
  pipeline. Publii has no CLI, so rendering and syncing were interface actions
  that a human had to perform between two automated steps.
- CLI commands `desktop-status` and `desktop-sync`, emitting the same JSON shape
  as the rest of the tool.
- Optional `desktop` extra: `pip install "publii-agent-toolkit[desktop]"`. The
  core package keeps pydantic as its only runtime dependency.

### How the desktop driver works

Publii is an Electron application, and Chromium only builds its accessibility
tree when something asks for it. Launched normally, Publii exposes nine empty
panes to Windows UI Automation and not one button. Launched with
`--force-renderer-accessibility` it exposes the whole interface by name. The
driver therefore always launches Publii itself rather than attaching to a window
that may have started without the flag.

Success is read from disk, never inferred from a click. Publii writes `syncDate`
into `input/config/site.config.json` when a sync completes; the driver compares
that value before and after and only reports success when it changes.

### Safety

A sync publishes to the live site, and the transaction backups this toolkit
takes protect `input/` — they do not undo a publication. The driver therefore
defaults to a dry run, refuses to start while Publii is already open, refuses to
click any element whose label contains "delete" (the site list places a "Delete
website" link beside every entry), and re-checks the selected site name
immediately before publishing.

### Known trap, handled

The sidebar link and the confirmation modal's button carry the *identical* label
"Sync your website". Only the control type separates them: `Hyperlink` in the
sidebar, `Text` in the modal. Clicking the sidebar element twice reopens the
modal and publishes nothing, while looking like a click that worked. The driver
matches on control type and waits for the modal title before confirming.

## [1.0.0] — 2026-08-31

### Added

- Initial release: safe programmatic editing of a Publii site.
- `models`, `repository`, `sync` and `agent` layers.
- `PubliiTransaction`: timestamped backup, single SQLite transaction, atomic
  JSON swap, integrity check before commit, full rollback on any failure.
- `publii-agent` CLI with `ops`, `read`, `plan`, `apply` and `check`.
