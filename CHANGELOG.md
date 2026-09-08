# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
