# Changelog

## [Unreleased]

### Regret client

This release introduces Consequences, because apparently drawing terrible cards was not enough.

- Added opt-in **Consequences** controls for voting and vote telemetry.
- **Enjoy Consequences** enables voting and stable-ID pseudonymous vote telemetry.
- **Regret Consequences** disables voting entirely, including vote telemetry.
- Existing installations without a saved preference are prompted once; the choice is stored locally and preserved by future upgrades.
- Added consent schema versioning so materially different future telemetry can require renewed consent.
- Added a cached, best-effort PyPI update check. It is separate from Consequences, does not use the stable ID, runs at most daily, and never installs updates automatically.
- Non-interactive invocations remain Consequences-disabled unless an explicit preference already exists.

No account is required. When enabled, voting may use a stable pseudonymous installation identifier. When disabled, voting and vote telemetry are both disabled. We are not constructing a shadow profile; we have neither the budget nor the emotional resilience.

The stew remains unaccountable. Regret remains appropriately named.

## Release history

Previous release notes are recorded in `patchnotes.md`.
