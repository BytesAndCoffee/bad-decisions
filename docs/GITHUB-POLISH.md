# GitHub launch settings

Repository settings are not versioned with the source. Apply these manually in
GitHub after reviewing them.

## About panel

- **Description:** An absurdly overengineered open-source party game engine, with a terminal client called Regret.
- **Homepage:** `https://bytes.coffee/bad-decisions/web/`
- **Topics:** `party-game`, `cli`, `terminal`, `python`, `openapi`, `self-hosted`, `multiplayer`, `homebrew`, `rest-api`, `game-engine`
- Keep Issues enabled. Discussions are enabled; use Issues for reproducible bugs
  and concrete feature requests, and keep Discussions lightweight with
  **General**, **Show and tell**, and **Packs / integrations** categories.

GitHub identifies the repository license as “Other” because the MIT software
license contains an explicit card-content boundary. Moving that footer could
improve SPDX detection, but the repository's established policy requires the
license text to remain unchanged. Preserve the clearer legal boundary rather
than optimizing the badge.

## Social preview

Upload [`assets/social-preview.png`](assets/social-preview.png). The editable
source is [`assets/social-preview.svg`](assets/social-preview.svg). The design
deliberately uses Bad Decisions' cream,
charcoal, and warm-orange identity without borrowing third-party card-game
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

The formula has a stable tagged sdist, immutable checksum, minimal Python-only
dependency chain, a useful offline test, installed manual page, and canonical
project URLs. Shell completion is the principal remaining nicety for eventual
core readiness; it is not a launch blocker.

## Package maturity

The server and client intentionally retain `Development Status :: 5 -
Production/Stable`: the public API and CardDeck formats are versioned, migration
boundaries are documented, releases are lockstep and CI-published, installation
is smoke-tested from exact wheels, and the hosted service uses guarded rollback.
The project's age alone is not a reason to advertise weaker compatibility
expectations.
