# Changelog

## [Unreleased]

## [1.6.2]

### Changed

- Cut the AWS stack's idle cost to the minimum while keeping horizontal scaling: an API Gateway HTTP API with a VPC link and Cloud Map replaces the ALB, tasks run in public subnets (admitting only the VPC link) instead of behind four interface endpoints, and tasks are the smallest Fargate size (0.25 vCPU, 512 MiB) on Fargate Spot by default. `deploy aws --capacity on-demand` opts out of Spot and is saved.
- The API is throttled to 50 requests/s (burst 100), and a custom domain disables the generated `execute-api` endpoint.
- Tasks report health through a Python container health check, since no load balancer probes them.
- `setup aws` bootstraps CDK only when the `CDKToolkit` stack is missing (or with `--bootstrap`), so it runs as a non-root IAM user without IAM permissions. It also keeps only the newest 10 ECR images.
- Superseded S3 object versions expire after 30 days.

## [1.6.1]

### Changed

- `setup aws` is now one-time account preparation that is safe to repeat: it creates the management secret only if missing, never rotates an existing token, no longer builds images, and merges into `~/.bad-decisions.env` instead of overwriting deploy outputs and domain settings.
- `deploy aws` builds and pushes the image for the installed version, shows `cdk diff`, and asks before deploying (`--yes` for unattended runs, `--source` to build a checkout). Domain flags passed to it are saved.

### Added

- `rotate-token aws` replaces the management token and forces a new ECS deployment so every task uses it.

## [1.6.0]

### Added

- Completed the pip-installed AWS-native deployment: ECS/Fargate, certificate-matched ALB/Route 53 HTTPS, private S3, CloudFront archives, dynamic Lambda catalog, DynamoDB Consequences, Secrets Manager, autoscaling, and local management commands.
- Added atomic AWS CardDeck publication, bundled-pack seeding, catalog listing, and private AWS Consequences reports.
- Reduced AWS defaults to one task, scale-to-two, one-AZ interface endpoints, and no default Container Insights while retaining private compute.

### Fixed

- Removed the obsolete in-container `curl` health check that caused healthy Fargate tasks to trip the ECS deployment circuit breaker.

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
