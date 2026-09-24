# Changelog

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
