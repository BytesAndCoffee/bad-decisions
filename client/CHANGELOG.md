# Changelog

## [Unreleased]

## [2.0.3]

### Changed

- Reworked the PyPI README around Regret's joke-first quick start, documented
  the hosted service and privacy controls more clearly, and added complete
  project discovery metadata.
- Lockstep release with the server's public-launch documentation, creator
  example, community-health, and warmer browser palette work.

## [2.0.2]

### Changed

- Lockstep release with the server’s burnt-sienna web palette; the terminal client is otherwise unchanged.

## [2.0.1]

### Fixed

- `regret feedback clear` reported "API returned something other than JSON" although the server had cleared the vote: an empty success response (204 No Content) is now treated as success.

## [2.0.0] - Bad Decisions 2.0: Terrible choices at terrifying speeds

### Added

- Full support for the version-2 REST API and Peer Pressure protocol.
- `regret doctor` verifies the install, manual-page discovery, local
  preference permissions, and service compatibility without changing state.
- Homebrew installation is the recommended macOS/Linux path; pipx remains
  supported.

### Changed

- Pack selectors are now `--prompt-packs` and `--answer-packs`, and output
  consistently uses prompt/answer terminology.
- Peer Pressure rejoin uses its bearer session token alone, mutations carry
  UUID request IDs and expected revisions, and heartbeats resynchronize from
  the authoritative room projection.
- Python 3.10 is now the minimum supported client runtime. The client remains
  dependency-free.

### Removed

- Version-1 API support and the legacy black/white selector flags.

### Fixed

- Feedback URLs remain correct behind a reverse-proxy path prefix.
- Peer Pressure refusals display their structured reason and useful recovery
  hints instead of a bare HTTP status.

## [1.8.5]

### Added

- `regret --version`.
- The wheel installs `regret(1)` into `share/man/man1`, so `man regret` works after a `--user`, venv, or pipx install. The page moved to `client/man/` and ships in the client sdist.

### Changed

- `regret(1)` documents `together --timeout`, the Peer Pressure prompt flow and refusal messages, the `consequences enable`/`disable` aliases, and exit status 2.

## [1.8.4]

### Fixed

- `regret together` reported Peer Pressure refusals as a bare `HTTP 409`: the protocol NACK's reason and message are now shown, with a hint for a taken display name or a table that already started, and stale-revision refusals trigger the intended resync.

## [1.8.3]

### Changed

- Lockstep release with the server's Peer Pressure fixes (Responsible Adult handoff, streamed cards); no client changes.

## [1.8.2]

### Changed

- Lockstep release with the server's prefix-safe trailing-slash redirects and rootless deployment diagnostics; no client changes.

## [1.8.1]

### Changed

- Lockstep release with the server's rootless rollback and pip-only local redeploys. 1.8.0 was tagged but not published to PyPI, so this is the first published release with `regret together`.

## [1.8.0]

Tagged but not published to PyPI; see 1.8.1.

### Added

- Added `regret together ROOM_ID`, the interactive Peer Pressure multiplayer client with protected reconnect sessions, background heartbeats, and automatic authoritative resynchronization.

## [1.7.1]

### Changed

- Lockstep release with the Bad Decisions server package.

## [1.7.0]

### Changed

- Lockstep release with the Bad Decisions server package.

## [1.6.4]

### Changed

- Lockstep release with the Bad Decisions server package.

## [1.4.1]

### Fixed

- Updated release-workflow documentation tests for the reorganized `docs/` tree.

## [1.4.0]

### Added

- Consequences stores private raw card-text snapshots alongside stable hashes for owner analytics and TUI lookup.
- Added answer-card statistics, sortable dashboard views, and hash/value display controls.
- Reorganized long-form documentation under `docs/` and added comprehensive `bad-decisions(1)` and `regret(1)` manpages.

## [1.3.1]

### Changed

- Moved the Consequences Textual dashboard into the core server package; no extra install is required.

## [1.3.0]

### Added

- Added answer-card frequency statistics, sortable TUI views, and optional local hash-to-card value resolution.
- Added an interactive Textual Consequences report dashboard with dashboard, combination, prompt, and recent-draw views plus row drill-downs.

## [1.2.2]

### Added

- Added a persistent website control for reopening and changing the Consequences preference.

## [1.2.1]

### Added

- Added a first-visit Consequences disclaimer modal to the web client. Visitors can enjoy or regret Consequences before voting and pseudonymous telemetry are enabled.

## [1.2.0]

### Known issue

A good decision was made during release engineering.
Root cause analysis is ongoing.
No recurrence is expected.

### Fixed

- Removed an unnecessary PyPI version check in favor of the server’s own version endpoint.
- Unfortunately, this was the correct thing to do.

## [1.1.9]

This entry describes the terminal client changes; the engine and service changelog is maintained at the repository root.

### Regret client

This release introduces Consequences, because apparently drawing terrible cards was not enough.

- Added opt-in **Consequences** controls for voting and vote telemetry.
- **Enjoy Consequences** enables voting and stable-ID pseudonymous vote telemetry.
- **Regret Consequences** disables voting entirely, including vote telemetry.
- Existing installations without a saved preference are prompted once; the choice is stored locally and preserved by future upgrades.
- Added consent schema versioning so materially different future telemetry can require renewed consent.
- Added a cached, best-effort API version check. It is separate from Consequences, does not use the stable ID, runs at most daily, and never installs updates automatically.
- Non-interactive invocations remain Consequences-disabled unless an explicit preference already exists.

No account is required. When enabled, voting may use a stable pseudonymous installation identifier. When disabled, voting and vote telemetry are both disabled. We are not constructing a shadow profile; we have neither the budget nor the emotional resilience.

The stew remains unaccountable. Regret remains appropriately named.

## Release history

Previous release notes are recorded in `patchnotes.md`.
