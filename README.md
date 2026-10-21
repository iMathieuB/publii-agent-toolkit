# publii-agent-toolkit

Read and edit a [Publii](https://getpublii.com/) site from code, safely enough to hand the keys to an AI agent.

Publii is a desktop static-site CMS. Its content lives in a SQLite database and a handful of JSON config files, which makes it very scriptable and very easy to corrupt. Write a menu entry pointing at a post id that does not exist and Publii will not open the site. There is no schema validation and no undo.

This toolkit is the layer that makes automated editing survivable. It is useful from a plain Python script, and it exists mostly because agents are now writing those scripts.

## Start here / Pour commencer

Paste this one instruction into Claude Code, Codex, or any AI agent that can run commands on your computer:

> Install publii-agent-toolkit from https://github.com/iMathieuB/publii-agent-toolkit, read its AGENTS.md, and ask my permission before every install, every write and every publish.

Collez cette seule consigne dans Claude Code, Codex ou tout agent IA qui peut lancer des commandes sur votre ordinateur :

> Installez publii-agent-toolkit depuis https://github.com/iMathieuB/publii-agent-toolkit, lisez son AGENTS.md et demandez ma permission avant chaque installation, chaque écriture et chaque publication.

It needs Python 3.10 or newer. If Python is missing, the agent can install it with your permission (on Windows: `winget install Python.Python.3.12`). / Il faut Python 3.10 ou plus récent ; s'il manque, l'agent peut l'installer avec votre permission (sous Windows : `winget install Python.Python.3.12`).

Four ready-made skills for Claude, in French, go with this toolkit: one puts a five-page Publii site online on Cloudflare R2, one adds a contact form that emails each message through Cloudflare, one writes pages that Google and AI assistants can read, one submits the sitemap to Google and Bing. See [`skills/`](skills/README.md). / Quatre skills pour Claude, en français, accompagnent ce toolkit : voir [`skills/`](skills/README.md).

## The safety argument

An agent driving a CMS through a Python API has to be trusted not to write bad Python. An agent driving it through a fixed set of JSON commands only has to produce valid JSON, and every command it can express is one the toolkit already knows how to validate, stage and roll back.

The guarantees do not depend on the agent behaving well:

**A plan is separate from its application.** `plan` describes what would happen and touches nothing. Only `apply` writes.

**Writes take the database lock first.** If Publii has the site open, the run fails immediately with `site_locked` rather than fighting over the file.

**The whole mutable surface is backed up before any change**, to `input_backup/<timestamp>/`. Database and config together, because restoring one without the other leaves them out of step. Two changes within the same second get two folders (`<timestamp>-2`), never one overwritten.

**Everything happens in one transaction.** Database changes commit or roll back as a unit, and the config files are swapped atomically with `os.replace` only after the commit succeeds.

**Images are staged like the rest.** They live outside the backed-up surface, so a copied image waits under a temporary name, is moved into place just before the commit, and is deleted, along with any folder created for it, if the batch fails. An existing file is never overwritten.

**A structural integrity check runs before the commit, not after.** Dangling menu links, duplicate slugs, orphaned tag rows, `mainTag` pointing at a tag that does not exist, missing tables or columns after a Publii upgrade. Any of those and the whole batch rolls back and the backup is restored.

**Reads use a read-only connection**, so a bug in the read path cannot write.

The worst outcome of a confused agent is a refused command and a log line.

## Install

The package is not on PyPI. Install it from GitHub:

```bash
pip install "publii-agent-toolkit @ https://github.com/iMathieuB/publii-agent-toolkit/archive/refs/heads/main.zip"
```

Python 3.10 or newer. The only dependency is pydantic. The `.zip` address needs no Git; `git+https://github.com/iMathieuB/publii-agent-toolkit.git` works too where Git is installed.

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
| `get_site_settings` | read | none; returns the settings below, where the server settings point (with `keys_set`, a yes or no), the theme's menu positions and the editors in use, never a credential |
| `check` | read | none |
| `create_page` | write | `title`, `slug` (unique), `text`, and optionally `editor` (`tinymce` for HTML, `markdown`), `status` (`published`\|`draft`), `metaTitle`, `metaDesc`, `parent_id` or `parent` (slug) |
| `update_post` | write | `slug`\|`id`, and one or more of `title`, `text`, `status` |
| `update_meta` | write | `slug`\|`id`, and one or more of `metaTitle`, `metaDesc`, `metaRobots`, `canonicalUrl` |
| `set_tags` | write | `slug`\|`id`, `tags` (list of tag slugs) |
| `create_menu` | write | `position` (one the theme declares, e.g. `mainMenu`), `name` |
| `add_menu_item` | write | `position`, `label`, and `link` (post id, URL or `#`) or `page` (a page slug); `type`, `parent_id` |
| `remove_menu_item` | write | `position`, `id` |
| `update_menu_item` | write | `position`, `id`, and one or both of `label`, `title`; the entry keeps its place and its target |
| `update_site_settings` | write | one or more of `site_name`, `site_description`, `logo`, `cookie_banner`, `privacy_policy_page`, `front_page`; any other key is refused |
| `set_s3_deployment` | write | `website_url`, `endpoint` (without the bucket), `bucket`, and optionally `region` (default `auto`); the Access ID and the Secret key are refused |
| `add_media` | write | `file` (local image), `target` (`logo` or `post`), `slug`\|`id` for a post, and optionally `name`, `featured`, `alt` |

`publii-agent ops` returns this catalogue as JSON, which is the version to trust.

A later command in a batch can refer, by slug, to a page an earlier command creates. A five-page site with its menus, logo and cookie banner is one plan and one apply, so one backup and one transaction: [AGENTS.md](AGENTS.md) has the full example.

`update_site_settings` is a whitelist on purpose. `site.config.json` also holds the deployment credentials (S3 keys, FTP passwords, tokens); none of it can be changed through this tool, and nothing from that file is ever printed except the whitelisted values. `set_s3_deployment` is the one exception, and a narrow one: it fills the S3 server settings an agent already knows after preparing the bucket (address, endpoint, bucket, region) and refuses the two keys. The keys, the first render and the first sync stay with the site owner, in Publii.

## Publishing without the click

Publii has no CLI. Rendering and syncing are interface actions, which leaves a human pressing a button in the middle of an otherwise scriptable pipeline. The `desktop` module drives the application itself, on Windows.

```bash
pip install "publii-agent-toolkit[desktop] @ https://github.com/iMathieuB/publii-agent-toolkit/archive/refs/heads/main.zip"
python -c "import pywinauto"   # "DLL load failed": install the Microsoft Visual C++ Redistributable

publii-agent desktop-status
publii-agent desktop-sync --name my-site                     # dry run
publii-agent desktop-sync --name my-site --name my-site-en --apply
```

Two things make this reliable rather than a pile of coordinates.

Publii is an Electron application, and Chromium only builds its accessibility tree when something asks for it. Launched normally, Publii exposes nine empty panes to Windows UI Automation and not one button. Launched with `--force-renderer-accessibility` it exposes the whole interface by name. That is why the driver always launches Publii itself instead of attaching to a window that may have started without the flag, and why it refuses to run while Publii is already open.

Success comes from disk, not from the click. Publii writes `syncDate` into `input/config/site.config.json` when a sync completes, so the driver reads that value before and after and only reports success when it changes. A click that silently fails is detected instead of assumed to have worked — which matters, because the sidebar link and the confirmation modal's button carry the *identical* label, "Sync your website", separated only by control type. Clicking the wrong one reopens the modal and publishes nothing.

A sync publishes to the live site, and the backups this toolkit takes protect `input/` — they do not undo a publication. So `--apply` is required, any element labelled "delete" is refused outright (the site list puts a "Delete website" link beside every entry), and the selected site is re-checked immediately before publishing.

One Publii dialog would stop the driver, so the driver looks for its cause before launching Publii. When a site's cookie banner is enabled without `advanced.gdpr.settingsVersion`, Publii opens "We've made significant changes to the Privacy Settings" each time the site loads, right over the sync button. `desktop-sync` refuses such a site, dry run included, and says how to clear it: save the site settings once in Publii. Since 1.3.3, `update_site_settings` writes the marker itself.

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

**Close Publii.** `apply` refuses with `publii_running` while the app is open on Windows, and with `site_locked` if anything holds the database. Publii keeps the site's configuration in memory and writes it back when you save a setting, so a change made under it could be lost.

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
