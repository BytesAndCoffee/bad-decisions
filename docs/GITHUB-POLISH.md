# GitHub launch settings

Repository settings are not versioned with the source. Apply these manually in
GitHub after reviewing them.

## About panel

- **Description:** An absurdly overengineered open-source party game engine, with a terminal client called Regret.
- **Homepage:** `https://bytes.coffee/bad-decisions/web/`
- **Topics:** `party-game`, `cli`, `terminal`, `python`, `openapi`, `self-hosted`, `multiplayer`, `homebrew`, `rest-api`, `game-engine`
- Keep Issues enabled. Enable Discussions only if the maintainer wants a place
  for pack showcases and third-party client experiments that are not bug reports.

GitHub currently identifies the repository license as “Other” because the MIT
software license contains an explicit card-content boundary. That boundary is
intentional; do not simplify it in a way that places packs under MIT.

## Social preview

Upload [`assets/social-preview.svg`](assets/social-preview.svg) after exporting
it to a 1280×640 PNG. The design deliberately uses Bad Decisions' cream,
charcoal, and warm burnt-sienna identity without borrowing third-party card-game
logos or trade dress.

Recommended alt text:

> Bad Decisions — an absurdly overengineered party game engine. Terminal prompt: regret deal.

## Release presentation

GitHub's tag workflow publishes PyPI artifacts but does not create a GitHub
Release automatically. For each public release, create a release whose first
screen answers:

1. What changed and why should a user care?
2. Is the upgrade compatible?
3. Is migration required?
4. How is it installed?

Use [`RELEASE_NOTES_TEMPLATE.md`](RELEASE_NOTES_TEMPLATE.md), link to the full
changelog, and mark only the newest stable release as latest.

## Homebrew readiness

The supported route remains `bytesandcoffee/tap/regret`; do not submit to
Homebrew/core yet. Before each release announcement, confirm the formula uses
the current immutable PyPI sdist and passes:

```bash
brew install --build-from-source bytesandcoffee/tap/regret
brew test bytesandcoffee/tap/regret
brew audit --strict --formula bytesandcoffee/tap/regret
regret --version
regret health
man regret
```

Shell completion is the principal remaining nicety for eventual core readiness;
it is not a launch blocker.
