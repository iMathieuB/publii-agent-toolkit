# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.5] — 2026-10-21

### Fixed

- **Accented text sent on stdin arrived garbled on Windows.** The CLI read stdin with `sys.stdin.read()`, which Python decodes with the Windows code page (cp1252) unless UTF-8 mode is on, so the "débutants" an agent sends as UTF-8 was stored as "dÃ©butants" and showed in the published page titles. Found in a rehearsal on 2026-10-21. Stdin is now read as bytes and decoded as UTF-8 (a UTF-8 or UTF-16 byte-order mark is honoured, and the code page is used only for bytes that are not valid UTF-8). A batch can also be passed as `@file.json`. The JSON the CLI prints is now plain ASCII with `\u` escapes, the same bytes in every console code page.
- **Garbled text is refused before anything is written.** Every command's strings are checked for words that read as UTF-8 decoded as cp1252: such a word encodes back to cp1252 bytes forming valid UTF-8, which a genuine accented word never does, so real French, Portuguese and symbols such as € or → pass. The error says how to resend the batch.

### Added

- `set_theme_style` takes `title_alignment` (the theme's `alignHero`: `center` centres the header of every page).
- The `site-publii-cloudflare` skill watches the browser tab while the person signs in (Cloudflare, Google, Bing) and carries on as soon as the signed-in page appears, instead of asking to be told; it centres page titles by default, and explains how to pass batches as UTF-8.

## [1.3.4] — 2026-10-21

### Added

- **`set_s3_deployment`**: fills Publii's "S3 compatible storage" server settings, all but the two keys, so the site owner only pastes the Access ID and the Secret key. It takes `website_url`, `endpoint` (without the bucket), `bucket` and `region` (default `auto`), and writes the values Publii itself writes for Cloudflare R2: `domain`, `deployment.protocol` `s3`, and under `deployment.s3` `customProvider` true, `provider` `custom`, `endpoint`, `bucket`, an empty `region` and `customRegion`. These were read from a site saved in Publii 0.48.1 and one saved in 0.47.6 on 2026-10-21, with the same result in both. Prefix, ACL and the Cache-Control headers are left to Publii's defaults. The keys are refused under any of their usual names, keys already filled are kept, and a site set to another server type is refused. Asked for while preparing a video demonstration: the agent had created the bucket and knew every value but the keys, and the owner was retyping them by hand.
- The `site-publii-cloudflare` skill uses it: once the bucket, its domain and the rewrite rule are in place, the agent fills the server settings (step 2.5), and the person creates the R2 token in the dashboard and pastes the two keys (step 2.6). The full table stays as the way to do it with an older toolkit.
- The `site-publii-cloudflare` skill proposes a hero image for the home page, for the person to approve: what it shows and why, its format, the alt text, and a prompt to paste in the person's own image generator (or keywords for a free photo library). The agent does not drive an image generator in the browser on the person's behalf, since such services often forbid it (OpenAI's terms rule out "automatically or programmatically extract data or Output"). The approved image goes in with `add_media` as the page's featured image, which the default theme shows as the hero, and the toolkit makes its smaller copies (below).
- **`add_media` makes the theme's smaller copies of a post image**, so the site can be published without a trip to Publii's "Regenerate thumbnails". Publii writes a copy per width the theme declares when someone adds an image in the app, and the theme's srcset points at them; an image copied in by the toolkit had none, so the srcset pointed at missing files. New module `images.py` reads the widths from the theme's `config.json` (`files.responsiveImages`, `featuredImages` for a featured image, `contentImages` otherwise) and the quality from the site settings, and writes `responsive/<name>-<size>` with the height rounded from the ratio and nothing enlarged. On 2026-10-21, for the same 2752 x 1536 photo, Publii 0.48.1 and the toolkit gave the same six names and the same dimensions, and file sizes within 3 %. The copies are staged in the same transaction as the image; a copy already on disk (Publii's own) is kept; sizes with a fixed height (crops) are left to Publii and listed. Needs the new optional `images` extra (Pillow); without it, the image goes in as before and `responsive.note` says why no copies were made. GIF, SVG, logos and sites with WebP conversion turned on get no copies.
- **`set_theme_style`**: a presentable default look. It sets the theme's primary colour, colour scheme, fonts and footer text, each checked against the options the active theme declares, and with `menu_style: "pro"` adds a menu style for Publii's default theme: a line under the header, bolder entries underlined in the primary colour on hover and on the current page, and the last entry as a rounded button. The style is a block between markers in `input/config/custom-css.css` (the file of Publii's Tools, Custom CSS), rewritten on each call and removed with `"none"`; the rest of the file is kept. A primary colour too light for white text (under 3:1) is refused. `PubliiTransaction` gains `set_custom_css`, staged and swapped after the commit like the JSON files. Checked by rendering the demonstration site's home page with the block in headless Chrome.
- Skills: `site-publii-cloudflare` now reads a site information sheet (`fiche_site.md` or similar) before writing and asks only what it leaves open, in one message, with announced defaults (visual editor, no cookie banner without trackers, the only logo in the folder); tells the person up front that the app may ask for permissions along the way; uses Cloudflare's MCP server first and the browser only for what the API cannot do, opening the right page itself; for the R2 token, fills in the creation page (name, Object Read & Write, the one bucket) for the person to click Create, then stops reading or capturing the page until the keys are pasted in Publii; and proposes a look with `set_theme_style` (colour from the logo, light scheme for a dark logo, theme fonts, footer text, the menu style). `formulaire-contact-cloudflare` warns that the Claude app's automatic permission mode can hold the Turnstile widget, the Worker and its route until the person gives explicit consent in the conversation (seen in a rehearsal on 2026-10-21 and first blamed on Cloudflare), asks for that consent in one message before creating them, and prefers the MCP server for Email Routing and Turnstile.
- `get_site_settings` now has a `deployment` summary: protocol, website address and, for S3, endpoint, bucket, region and `keys_set`, a yes or no that never carries a key.

### Fixed

- **`site-publii-cloudflare` skill: connecting Cloudflare from the Code tab of the Claude desktop app.** The skill told the agent to have the person add Cloudflare's MCP server as a connector. On 2026-10-21 (desktop app 3.1623.1, Claude Code 2.1.304), a custom `mcp.cloudflare.com` connector added and authorized in claude.ai never reached a Code tab session, even a new one. The skill now checks the server's state and, when it is missing, adds it at user scope with `claude mcp add --transport http --scope user cloudflare https://mcp.cloudflare.com/mcp` (the bundled `claude.exe` lives under `%APPDATA%\Claude\claude-code\`, outside the PATH). The person then signs in with `/mcp`. It also warns that the older "Cloudflare Developer Platform" connector, which Cloudflare has deprecated, has no tools for bucket domains or rules, says that Wrangler cannot set the rewrite rule, and asks the agent to offer before driving the Cloudflare dashboard in the browser instead of the server.
- The R2 activation step now gives the dashboard labels read on 2026-10-20, in place of a placeholder.
- **`formulaire-contact-cloudflare`: the consent checkbox was invisible on Publii's default theme.** "Simple" hides native checkboxes (`opacity:0; position:absolute`) and draws its own in the label that follows (`input[type=checkbox]+label:before`). The form fragment put the checkbox inside its label, so no box was drawn: the field could still be ticked by clicking the text, but nobody could see it, and without it the Worker refuses the message. The fragment now puts the checkbox first and its `<label for="consentement">` right after, which also renders a normal checkbox on themes that do not restyle it. Found on 2026-10-21 while rehearsing the demonstration.

## [1.3.3] — 2026-10-20

Found by running the five-page example end to end on a fresh Windows 11 virtual machine, with Publii 0.48.1, the version getpublii.com now offers: install without Git, build with one plan and one apply, then `desktop-sync --apply` to an S3 endpoint (a local S3 emulator standing in for Cloudflare R2, so nothing went online).

### Fixed

- **Enabling the cookie banner made Publii stop on a dialog at every launch, which blocked `desktop-sync`.** Publii 0.40 and later read an enabled banner without `advanced.gdpr.settingsVersion` as a pre-0.40 configuration and open "We've made significant changes to the Privacy Settings (formerly GDPR) section" each time the site loads; the dialog covers the window until someone saves the site settings in Publii. `update_site_settings` set `advanced.gdpr.enabled` and left the marker empty, so a site built from the AGENTS.md example could not be published by `desktop-sync`. It now also writes `settingsVersion: "v2"` when it changes anything under `advanced.gdpr` and the marker is empty, which is what Publii's own "Save settings" writes; an existing value is kept. The test is the same in Publii 0.47.6 and 0.48.1 (`resources/app.asar`), so the defect dates from 1.3.0.
- **`desktop-sync` now refuses such a site before launching Publii**, dry run included, with the way out: open Site settings in Publii and click Save settings once. Sites written by 1.3.0 to 1.3.2 are the ones that need it.
- **`desktop-sync` could not publish with Publii 0.48.1.** Three controls changed since 0.47.6, the version the driver was built on: the site name at the top of the sidebar is now a Button (it was a Text, so the driver concluded no site was open), the modal's own "Sync your website" is now a Button (it was a Text, so the driver reported that the modal never appeared), and the success modal closes with "Close" instead of "OK". Each is now accepted in both forms, with the same guards as before: the modal's button is never a Hyperlink, which keeps it apart from the sidebar link of the same label, and "Close" is clicked only beside a known success message or over a covered sidebar. When the modal opens without a recognisable button, the error now says so instead of claiming it never opened. Nothing was published by the failed attempts: success is still read from `syncDate` on disk.

### Changed

- Install instructions use the GitHub `.zip` address instead of `git+https://`, which fails on a machine without Git, the usual state of a fresh Windows. AGENTS.md and the `site-publii-cloudflare` skill also cover the Microsoft Visual C++ Redistributable that pywinauto needs ("DLL load failed while importing win32ui" without it), the Microsoft Store stand-in that answers to `python` on a fresh Windows, and the terminal that does not see a Python installed after it was opened.

## [1.3.2] — 2026-10-20

No code changes: the Python package is the same as 1.3.1 apart from its version number.

### Added

- **`skills/formulaire-contact-cloudflare`**: adds a contact or waiting-list form to a static site hosted on Cloudflare, such as a Publii site on R2. A small Worker receives the form, checks the Turnstile token and emails each message to a verified destination address of the account, which Cloudflare's pricing page lists as free on every plan; the Worker keeps no copy. The skill also updates the privacy policy. `assets/` holds the Worker, a `wrangler.toml` template and the form fragment. It was tried on a real domain on 2026-10-20: the template deployed as is, the route answered before the site served on the same hostname (a Worker stood in for the site), and the first message landed in the spam folder, which the skill now warns about. Also attached as a ZIP to the GitHub release.

## [1.3.1] — 2026-10-16

### Added

- **`skills/`**: three skills for Claude, in French, for small-business owners in Quebec. `site-publii-cloudflare` puts a five-page Publii site online on Cloudflare R2 with this toolkit, `decouvrabilite-google-et-ia` writes a home page and service pages that search engines and AI assistants can read, and `soumission-sitemaps` checks the sitemap and walks through Google Search Console and Bing Webmaster Tools. Each is also attached as a ZIP to the GitHub release, for upload to claude.ai.

### Changed

- README: the French instruction now addresses the reader formally (« Installez… lisez… demandez »), like the rest of the French text.

### Fixed

- **Every read left a connection open on `db.sqlite` until garbage collection.** `PubliiRepository._connect_ro()` was used as `with self._connect_ro() as conn:`, but sqlite3's own context manager only ends a transaction; it never closes the connection. Each read kept a handle on the database file, which on Windows blocks renaming or replacing it, and Python 3.13+ reports each one as an unraisable exception. The defect dates from 1.0.0; the extra reads of 1.3.0 made the warnings show up in the test suite. Read connections are now closed when the block ends, and a test renames the database right after several reads to prove no handle is left.

## [1.3.0] — 2026-10-16

Until now the toolkit could only change a site that already had its pages and menus. This release lets an agent build one from a brand-new Publii site: pages, menus, logo, cookie banner and front page, in one plan and one apply, while the owner keeps the three steps that are theirs (the server credentials, the first render and the first sync). The use case that drove it is a small-business owner pasting a single instruction into an AI agent and watching it put a five-page site together.

Every Publii fact below was read from Publii 0.47.6 itself (`resources/app.asar`) or from live sites, and the source file is named in the code where it matters. What could not be confirmed that way is listed at the end of this entry.

### Added

- **`create_page`**, a JSON command over `PubliiTransaction.create_post(..., register_as_page=True)`. It writes the `posts` row, a `_core` blob with exactly the five keys Publii writes for a page, and the `pages.config.json` entry, in one transaction. The slug must be one Publii's own slug rules would keep, unique on the site, and not a folder name the URL scheme reserves. The editor matters because it decides how `text` is stored: Publii offers WYSIWYG (`tinymce`, HTML), Markdown and a block editor per page, with no site-wide default, and a new site has no page to copy the choice from. So the editor is inferred only when every existing row uses the same one, and must be given otherwise; the block editor's JSON format is refused. A `tinymce` page whose text is not HTML is refused too, since it would display as raw text.
- **`create_menu`.** A new Publii site starts with `menu.config.json` holding `[]`, so `add_menu_item` had nothing to add to. Positions are limited to the ones the active theme declares; the menu object copies Publii's shape, `maxLevels` stored as a string as on every live site checked.
- **`update_site_settings`**, a strict whitelist: `site_name` (`displayName`), `site_description` (`advanced.metaDescription`), `logo` (`config.logo` in `theme.config.json`; the `logo` in `site.config.json` is only the icon in Publii's own site list), `cookie_banner` (`advanced.gdpr.enabled`), `privacy_policy_page` and `front_page`. Any other key is refused, because `site.config.json` also holds the deployment credentials; only the whitelisted values are ever echoed back. Values are set inside the existing objects, so everything else round-trips untouched. On a brand-new site, which has no `advanced` object, partial objects are written: Publii deep-merges its defaults over both files when it reads them. `front_page` was not in the original request; it is there because without it a new site's root shows a blog listing instead of the home page.
- **`add_media`** copies a local JPEG, PNG, WebP, GIF or SVG into `media/website/` (logo) or `media/posts/<id>/` (a page or post) and returns the value to store: `media/website/<file>` for the logo, `#DOMAIN_NAME#<file>` inside page text. The first bytes must match the extension, an SVG carrying script or event handlers is refused, the name must be a bare file name, and a different file is never overwritten (the same bytes again are a no-op). `featured: true` also makes it the page's featured image.
- **`get_site_settings`**, a read: the whitelisted settings, the theme's menu positions and the editors in use, so an agent can read before it writes. No credential can reach its output.
- **References within a batch.** Plan checks steps in order against the site plus what earlier steps would create, and write commands resolve pages through the open transaction instead of the read-only connection, which could not see uncommitted rows. A page created in step 1 can be linked from `add_menu_item` (new `page` argument, a slug) or `update_site_settings` in step 2.
- **Media in the transaction.** Images are outside the `input/` backup, so `PubliiTransaction.stage_media()` copies them under a temporary name, moves them into place just before COMMIT (an unreferenced file is harmless, a committed reference to a missing one is not) and deletes them, with any folder it created, on abort.
- `apply` returns the backup folder as `result.backup`. AGENTS.md always told agents to report it; nothing gave it to them.
- `apply` refuses with `publii_running` while the Publii app is open on Windows. Publii keeps the site configuration in memory and writes it back when a setting is saved, and a config-only change never touches the database lock that was the only guard.
- README: a first section, in English and French, with the one instruction an owner pastes into an agent. AGENTS.md: installing, asking permission, the new commands, and a worked example building a five-page site; a test runs that example as written.
- Tests: 74 new, over the cases above, including plan writing nothing, a batch with one bad command landing nothing (rows, page tree, media), whitelist refusals, the round trip of unmodelled and credential keys, and no secret in any output.

### Fixed

- **Two menu entries added in one batch got the same id.** Both ids were computed from the menu file on disk, which the first entry had not reached yet. Ids now come from the menus as staged in the transaction.
- **A failed lookup inside the transaction escaped as a traceback.** An unknown tag slug or a missing parent page raises `ValueError` in the transaction; `apply` only caught `CommandError`. Both now return `bad_command`.
- **Nested pages were reported as unlisted.** `pages.config.json` is a tree, and the integrity check read only its top level; on sites with subpages every child page raised a warning. `register_page` also only looked for a parent at the top level.
- **The integrity check accepted a `pages.config.json` entry without a `subpages` list**, which Publii's renderer walks unconditionally. It is now an error. The test fixture had such entries; it now has Publii's real shape, an `AUTOINCREMENT` posts table and a theme.
- `__version__` said 1.1.0 in the 1.2.0 release.
- Desktop: two fixes made in the Ordinata copy of the driver had not been brought back here. The site list is polled for up to 10 seconds instead of read once after 2.5 (it was seen empty at 2.5 s and complete at 4 s), and an entry whose text is another project's display name is no longer a candidate ("Acme" matched "Acme EN").

### Changed

- When no site is open in Publii, the desktop driver now looks for the site in the list already on screen instead of stopping. Publii opens the last site it showed, from its own localStorage, and its site list when there is none.
- The integrity check also reports, as warnings, a cookie banner pointing at a missing page, a front page that is not a page, and a theme logo whose file is missing. Warnings rather than errors because what Publii does with each has not been verified; `update_site_settings` refuses such values outright.
- `pytest` runs from a checkout without installing the package (`pythonpath = ["src"]`).

### Not verified

- No page, menu or setting written by this release has been rendered or synced by Publii yet: confirming that needs a live launch, which was not done against real sites. The rows and files copy Publii's own shapes, read from its source and from live sites, and one full batch was applied to a temporary copy of a real site, where the integrity check passed and the deployment block came back byte-identical.
- Publii writes a `pageViewSettings` row when it saves a page; `create_page` does not. Publii's renderer falls back to defaults when the row is absent (`renderer-cache.js`), which was read in the source, not observed.
- `add_media` does not make the responsive copies Publii creates on upload. How the default theme renders a featured image without them has not been checked; regenerating thumbnails in Publii should create them.
- The desktop driver's path for the "no site open" start screen, and its handling of a fresh per-user install, were worked out from Publii's source and covered by tests with stand-in elements. They have not been run against a live Publii.
- Whether Publii holds a database lock merely by being open was never established, which is why `apply` now checks for the process as well.

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
