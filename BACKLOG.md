# OmaWeb backlog

Small on purpose. OmaWeb is 1.0.0: the compile/publish path, Stylus adapter,
Dark Reader export and theme mixing are done. What is left is upstream watch
and distribution.

## Upstream watch

- **Re-run `omaweb scan` after each Catppuccin bundle release.** It maps new
  issues to style ids and lists styles with no `current-maintainers` entry.
  Last scan (bundle `2026-11-06T00:09:04Z`): 0 non-PR issues, 12 styles
  without an active maintainer — `arch-wiki`, `ecosia`, `formative`, `google`,
  `graphite`, `hyperpipe`, `invidious`, `libreddit`, `linkedin`,
  `microsoft-word`, `minesweeper`, `reddit`. This is a watch list, not a
  disable list; the user decides what to turn off.
- **`omaweb fetch` is manual.** A newer bundle is opt-in so a theme switch
  never waits on the network and generated assets never enter git. Keep it
  that way unless a release cadence makes auto-fetch worth the surprise.
- **One style compiles to nothing useful.** The current bundle has a style
  whose CSS produces no supported `@-moz-document` selector for Chromium;
  `omaweb status` counts it as a compile error and the other 135 styles are
  unaffected. Re-check after a bundle bump before considering any workaround.

## Distribution

- **Public repo:** `https://github.com/AlxWolfenstein97/omaweb`. Root
  `manifest.json`, README with previews, MIT license.
- **Marketplace review gate.** The Omarchy marketplace wants an exact-commit
  scan, the Automated Security Baseline, and an `approved-and-verified`
  maintainer. It is a review gate, not a security audit. Generated CSS and
  the Stylus/Dark Reader exports stay out of git so a theme or bundle change
  cannot invalidate the pinned commit.

## Deferred

- **Dark Reader automation.** The settings export is imported by hand after a
  theme switch. Automating it would mean either bundling Dark Reader's JS API
  in a companion extension (broad host access, a second theming engine) or
  patching Dark Reader's private extension storage (LevelDB/IndexedDB) with
  the browser closed. Both are fragile and larger than the problem; the manual
  import is one click and stays.
