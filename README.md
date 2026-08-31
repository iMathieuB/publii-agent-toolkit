# publii-agent-toolkit

Read and edit a [Publii](https://getpublii.com/) site from code, safely enough to hand the keys to an AI agent.

Publii is a desktop static-site CMS. Its content lives in a SQLite database and a handful of JSON config files, which makes it very scriptable and very easy to corrupt. Write a menu entry pointing at a post id that does not exist and Publii will not open the site. There is no schema validation and no undo.

This toolkit is the layer that makes automated editing survivable. It is useful from a plain Python script, and it exists mostly because agents are now writing those scripts.

## The safety argument

An agent driving a CMS through a Python API has to be trusted not to write bad Python. An agent driving it through a fixed set of JSON commands only has to produce valid JSON, and every command it can express is one the toolkit already knows how to validate, stage and roll back.

The guarantees do not depend on the agent behaving well:

**A plan is separate from its application.** `plan` describes what would happen and touches nothing. Only `apply` writes.

**Writes take the database lock first.** If Publii has the site open, the run fails immediately with `site_locked` rather than fighting over the file.

**The whole mutable surface is backed up before any change**, to `input_backup/<timestamp>/`. Database and config together, because restoring one without the other leaves them out of step.

**Everything happens in one transaction.** Database changes commit or roll back as a unit, and the config files are swapped atomically with `os.replace` only after the commit succeeds.

**A structural integrity check runs before the commit, not after.** Dangling menu links, duplicate slugs, orphaned tag rows, `mainTag` pointing at a tag that does not exist, missing tables or columns after a Publii upgrade. Any of those and the whole batch rolls back and the backup is restored.

**Reads use a read-only connection**, so a bug in the read path cannot write.

The worst outcome of a confused agent is a refused command and a log line.

## Install

```bash
pip install publii-agent-toolkit
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

`publii-agent ops` returns this catalogue as JSON, which is the version to trust.

## Layers

Use whichever fits. Each one is usable on its own.

```
agent       JSON commands, plan and apply       for a model, or a shell script
sync        compare two sites, propagate        for keeping a translated copy aligned
repository  reads, and PubliiTransaction        for anything the command set omits
models      pydantic schemas                    for validating a row or a config blob
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
