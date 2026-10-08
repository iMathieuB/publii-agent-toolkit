# publii-agent-toolkit

Read and edit a [Publii](https://getpublii.com/) site from code, safely enough to hand the keys to an AI agent.

Publii is a desktop static-site CMS. Its content lives in a SQLite database and a handful of JSON config files, which makes it very scriptable and very easy to corrupt. Write a menu entry pointing at a post id that does not exist and Publii will not open the site. There is no schema validation and no undo.

This toolkit is the layer that makes automated editing survivable. It is useful from a plain Python script, and it exists mostly because agents are now writing those scripts.

## The safety argument

An agent driving a CMS through a Python API has to be trusted not to write bad Python. An agent driving it through a fixed set of JSON commands only has to produce valid JSON, and every command it can express is one the toolkit already knows how to validate, stage and roll back.

The guarantees do not depend on the agent behaving well:

**A plan is separate from its application.** `plan` describes what would happen and touches nothing. Only `apply` writes.

**Writes take the database lock first.** If Publii has the site open, the run fails immediately with `site_locked` rather than fighting over the file.

**The whole mutable surface is backed up before any change**, to `input_backup/<timestamp>/`. Database and config together, because restoring one without the other leaves them out of step. Two changes within the same second get two folders (`<timestamp>-2`), never one overwritten.

**Everything happens in one transaction.** Database changes commit or roll back as a unit, and the config files are swapped atomically with `os.replace` only after the commit succeeds.

**A structural integrity check runs before the commit, not after.** Dangling menu links, duplicate slugs, orphaned tag rows, `mainTag` pointing at a tag that does not exist, missing tables or columns after a Publii upgrade. Any of those and the whole batch rolls back and the backup is restored.

**Reads use a read-only connection**, so a bug in the read path cannot write.

The worst outcome of a confused agent is a refused command and a log line.

## Install

The package is not on PyPI. Install it from GitHub:

```bash
pip install "git+https://github.com/iMathieuB/publii-agent-toolkit.git"
```

Python 3.10 or newer. The only dependency is pydantic.

## Use from the command line

Every subcommand takes JSON and returns JSON. Exit status is 0 when `ok` is true.

```bash
SITE=~/Documents/Publii/sites/my-site

# What commands exist?
publii-agent ops

# Is the site structurally sound right now?
publii-agent --site "$SITE" check

# Read
publii-agent --site "$SITE" read '{"op":"list_posts","pages":true}'
publii-agent --site "$SITE" read '{"op":"get_post","slug":"about"}'

# Preview a change
publii-agent --site "$SITE" plan '[{"op":"update_post","slug":"about","title":"About us"}]'

# Make it
publii-agent --site "$SITE" apply '[{"op":"update_post","slug":"about","title":"About us"}]'
```

Long payloads go on stdin:

```bash
cat changes.json | publii-agent --site "$SITE" apply
```

## Use from Python

```python
from publii_toolkit import AgentSession

session = AgentSession("~/Documents/Publii/sites/my-site")

pages = session.read({"op": "list_posts", "pages": True})["result"]

batch = [
    {"op": "update_post", "slug": "about", "title": "About us"},
    {"op": "update_meta", "slug": "about", "metaDesc": "Who we are."},
]

preview = session.plan(batch)
if preview["ok"]:
    print(session.apply(batch))
else:
    print(preview["result"]["problems"])
```

Or drop to the repository layer when you need something the command set does not cover:

```python
from publii_toolkit import PubliiRepository

repo = PubliiRepository.open("~/Documents/Publii/sites/my-site")

for post in repo.list_posts(pages=False):
    print(post.slug, len(post.text))

with repo.transaction() as tx:          # backup, lock, integrity gate, rollback
    tx.update_post_fields(3, title="A better title")
    tx.set_post_tags(3, ["guides"])
```

Leaving the `with` block runs the integrity check and commits. Raising inside it rolls everything back and restores the backup.

## Commands

| Command | Kind | Arguments |
|---|---|---|
| `list_posts` | read | `pages` (optional bool) |
| `get_post` | read | `slug` or `id` |
| `list_tags` | read | none |
| `list_menus` | read | none |
| `check` | read | none |
| `update_post` | write | `slug`\|`id`, and one or more of `title`, `text`, `status` |
| `update_meta` | write | `slug`\|`id`, and one or more of `metaTitle`, `metaDesc`, `metaRobots`, `canonicalUrl` |
| `set_tags` | write | `slug`\|`id`, `tags` (list of tag slugs) |
| `add_menu_item` | write | `position`, `label`, `link`, `type`, `parent_id` |
| `remove_menu_item` | write | `position`, `id` |
| `update_menu_item` | write | `position`, `id`, and one or both of `label`, `title`; the entry keeps its place and its target |

`publii-agent ops` returns this catalogue as JSON, which is the version to trust.

## Publishing without the click

Publii has no CLI. Rendering and syncing are interface actions, which leaves a human pressing a button in the middle of an otherwise scriptable pipeline. The `desktop` module drives the application itself, on Windows.

```bash
pip install "publii-agent-toolkit[desktop] @ git+https://github.com/iMathieuB/publii-agent-toolkit.git"

publii-agent desktop-status
publii-agent desktop-sync --name my-site                     # dry run
publii-agent desktop-sync --name my-site --name my-site-en --apply
```

Two things make this reliable rather than a pile of coordinates.

Publii is an Electron application, and Chromium only builds its accessibility tree when something asks for it. Launched normally, Publii exposes nine empty panes to Windows UI Automation and not one button. Launched with `--force-renderer-accessibility` it exposes the whole interface by name. That is why the driver always launches Publii itself instead of attaching to a window that may have started without the flag, and why it refuses to run while Publii is already open.

Success comes from disk, not from the click. Publii writes `syncDate` into `input/config/site.config.json` when a sync completes, so the driver reads that value before and after and only reports success when it changes. A click that silently fails is detected instead of assumed to have worked — which matters, because the sidebar link and the confirmation modal's button carry the *identical* label, "Sync your website", separated only by control type. Clicking the wrong one reopens the modal and publishes nothing.

A sync publishes to the live site, and the backups this toolkit takes protect `input/` — they do not undo a publication. So `--apply` is required, any element labelled "delete" is refused outright (the site list puts a "Delete website" link beside every entry), and the selected site is re-checked immediately before publishing.

## Layers

Use whichever fits. Each one is usable on its own.

```
desktop     render and publish through the app   for the step Publii offers no API for
agent       JSON commands, plan and apply        for a model, or a shell script
sync        compare two sites, propagate         for keeping a translated copy aligned
repository  reads, and PubliiTransaction         for anything the command set omits
models      pydantic schemas                     for validating a row or a config blob
```

`models` is worth a look even if you write your own tooling. The schemas were verified against live Publii sites, and they encode facts the Publii documentation does not state: a row in `posts` is a page when its `status` contains `is-page`; `posts_additional_data` is an entity-attribute-value table whose `_core` key holds a JSON blob of SEO fields; `mainTag` is stored sometimes as a string and sometimes as an integer, so both must round-trip. Post models use `extra="forbid"`, so a Publii upgrade that adds a column raises on read instead of being silently dropped. Config models use `extra="allow"`, so keys the toolkit does not model survive a read-modify-write untouched.

## For agents

[AGENTS.md](AGENTS.md) is the contract, written to be read by a model. Point Claude Code, Cowork, Cursor or anything similar at it.

## Before you run it

**Close Publii.** It holds the database open, and a write will fail with `site_locked` while it is running.

**Never edit `output/`.** It is generated. Publii overwrites it on the next sync.

**This edits a live content database.** The backups are real and tested, but take your own copy of the site folder the first time you use this on something you care about.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests build a complete Publii site on disk, with every table the integrity check expects, and run real transactions against it. They assert the safety properties directly: that a plan writes nothing, that a batch containing one bad command lands none of them, that the backup holds the pre-change state, and that an unmodelled config key survives a round trip.

## Licence

MIT. See [LICENSE](LICENSE).
