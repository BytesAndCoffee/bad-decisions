# Changelog

## [Unreleased]

### Changed

- Consequences SQLite schema v3: card text is stored once in a `contents`
  registry that rounds and combinations reference with enforced foreign keys;
  per-card draws and votes are cached in `content_stats` (a card counts once per
  round). The dashboard's prompt and answer views show one row per card text
  with a count of pack/card variants. `purge` also removes combinations and card
  text that no retained round references. Existing databases upgrade in place
  on the first write-mode open; content hashes are unchanged.

## [2.3.0] - The pressure is now a stream

### Changed

- Peer Pressure now delivers personalized room state to Regret and the browser
  through authenticated Server-Sent Events. Heartbeats remain separate as
  presence leases, and streams reconnect with bounded backoff and keepalives.
- Browser actions refused because another player advanced the room revision
  are retried up to three times only while the refreshed state proves the same
  action is still valid, matching Regret's concurrency behavior.
- CI now plays a complete four-browser Peer Pressure round over SSE, including
  concurrent submissions that exercise stale-revision recovery.

## [2.2.2] - The front door is the front door

### Changed

- The one-shot browser client is now the service front door at `/`; its static
  files live under `/assets/`, and former `/web` paths return HTTP 410 with the
  replacement path in the standard error envelope.

## [2.2.1] - Regret after dark

### Added

- The one-shot browser client and the Peer Pressure table have a dark mode in
  the same palette: warm near-black surfaces, cream text, and the burnt-orange
  accents. It follows the system setting by default; a footer toggle switches
  light or dark and remembers the choice in that browser.

## [2.2.0] - Peer Pressure escaped the terminal

### Added

- Peer Pressure is now a complete install-free browser multiplayer client at
  `/peerpressure`, with room creation and invitation links, reconnectable
  sessions, heartbeats, presence, private hands, anonymous judgment, scores,
  Responsible Adult rotation, and the complete room lifecycle.
- Room creators can choose an immutable deck from bundled custom packs and any
  indexed packs. The hosted browser defaults to the regular non-custom pack;
  later joiners use the room's established selection.
- The Peer Pressure API accepts an optional `packs` array when creating or
  joining (with `create`) a room, and every room projection reports the
  room's immutable selection as `room.packs`. Omitting `packs` still selects
  every pack in the loaded registry.
- Room hosts can end a room from Regret's line mode and TUI.

### Changed

- Peer Pressure is a prominent primary-navigation action rather than a landing
  page teaser.
- The Consequences consent dialog now explains that it enables optional card
  voting, states what is recorded with its identifier, and offers an explicit
  “Continue without voting” choice. The unexplained masthead shout was removed.
- Web assets now use a content-derived cache key, preventing same-version
  review deployments from mixing fresh HTML with stale immutable CSS or
  JavaScript.

### Upgrade notes

- The Peer Pressure room database schema is now version 5. Rooms that are live
  when a 2.1.x server is upgraded answer `410 room_expired`; players start a
  new room.

## [2.1.6]

### Documentation

- The website's terminal-room link now opens a dedicated Peer Pressure guide
  with Homebrew and pipx instructions for macOS and Linux, pipx instructions
  for Windows, and WinGet accurately marked as coming soon.

## [2.1.5]

### Changed

- The Peer Pressure website teaser now previews forthcoming browser multiplayer
  with a room code and player lobby, while clearly identifying terminal rooms
  as the multiplayer option available today.

## [2.1.4]

### Added

- The browser client now teases Peer Pressure multiplayer with the Regret TUI
  command and a link to the multiplayer API documentation.

### Fixed

- The Homebrew formula updater now clears formula-only revisions when moving
  to a new upstream release.

## [2.1.3]

### Fixed

- Pretend You're Xyzzy imports now produce plain card text: HTML character
  references such as `&reg;` and `&trade;` are decoded, `<br>` becomes a line
  break, and `<i>` italics are removed. Unexpected tags and invalid references
  fail the import. `scripts/clean_pyx_markup.py` applies the same cleaning to
  already-imported archives, changing only card text and recording the change
  in each pack's version and modifications.
- Peer Pressure clients retry an action refused for a stale revision only while
  it remains valid after resynchronizing. The line client also lets the
  Responsible Adult leave while judging and rejects out-of-range choices.
- Generated WinGet manifests include the required schema headers.

### Changed

- `bad-decisions pack replace-local ID URL --new-id ID` now updates a pack in
  place. The archive must declare a new version; the root activator retires
  the old file before linking the new one into its name, requires the service
  to report the new version, and restores the exact previous file on failure.
  Hosts need `sudo ./deploy.sh bootstrap-rootless` rerun to get this.

## [2.1.2]

### Fixed

- Create GitHub releases and attach their Windows assets in one operation so
  repositories with immutable releases enabled do not lock an empty release.

## [2.1.1]

### Added

- GitHub's native Windows runner now builds and smoke-tests a self-contained
  Regret executable. Each release carries a checksummed ZIP, generated WinGet
  manifests, and a Chocolatey package built around the same immutable binary.

## [2.1.0]

### Added

- Added an optional full-screen Textual interface to Regret's Peer Pressure
  client while preserving its dependency-free line-oriented default.

## [2.0.3]

### Added

- A joke-first project front door, canonical 60-second Regret guide, dedicated
  Consequences documentation, release-note template, community-health files,
  polished social-preview artwork, and a real-output terminal-demo workflow.
- A tested five-minute CardDeck creator example with original CC0 cards and
  exact license, attribution, export, validation, and import instructions.

### Changed

- Improved PyPI discovery metadata and documentation navigation for the server
  and client while preserving the MIT software and per-pack licensing boundary.
- Warmed the browser's burnt-sienna accent to a more orange `#bc552f` and
  retained the darker AA-contrast companion for small text.

## [2.0.2]

### Changed

- Reworked the web client’s complete orange accent system around a darker burnt-sienna palette, including the prompt and empty-state cards.

## [2.0.1]

### Fixed

- Lockstep release for the client fix: `regret feedback clear` no longer reports a 204 No Content response as an error. The server is unchanged apart from its version.

## [2.0.0] - Bad Decisions 2.0: Terrible choices at terrifying speeds

### Added

- A version-2 REST API and OpenAPI surface using prompts and answers throughout,
  with stable error envelopes, conditional pack responses, strict CORS origins,
  and bounded mutation rate limits.
- CardDeck pack schema 2, catalog schema 2, schema-1 read compatibility, exact
  license/attribution document matching, and reproducible audited tooling for
  rebuilding the complete public archive collection.
- Peer Pressure v2 sessions: bearer-token identity, explicit room ending,
  revision/resync heartbeats, bounded rooms and players, free-space admission,
  and server-authoritative ephemeral gameplay.
- Optional AWS deployment dependencies are split into `[aws]` and
  `[aws-deploy]`; the Textual Consequences dashboard remains in core.
- A 1.x-to-2.0 migration guide and comprehensive release manuals.

### Changed

- The project vocabulary is now prompt/answer: CLI selectors are
  `--prompt-packs` and `--answer-packs`, pack payloads contain `prompts`
  and `answers`, and prompt display text is `text`.
- Newly exported archives always use pack schema 2. Existing schema-1 packs are
  upgraded strictly in memory and remain importable.
- CardDeck catalogs expose prompt and answer counts and reject invalid
  manifests, checksums, pack identities, licenses, attribution, unsafe control
  characters, and hostile archive structures.
- Rootless deployment preflights the live registry with the candidate release,
  enforces disk and version gates, and provides explicit redeploy/older-version
  overrides.

### Removed

- The version-1 API. Every `/v1` route now returns HTTP 410 with an upgrade
  hint; callers must move to `/v2`.
- Legacy black/white CLI flags, the unused plain `bad-decisions setup`
  command, and obsolete deployment code.

### Fixed

- Peer Pressure no longer loops on impossible draws, accepts mismatched room
  schemas, shares idempotency keys between players, or stalls when its
  Responsible Adult disconnects.
- `regret feedback` no longer doubles a reverse-proxy path prefix.
- Catalog storage/network failures preserve the last good index, while
  archive-content failures are reported per object.

### Known issue

- A good decision was made during release engineering. Root cause analysis is
  ongoing. No recurrence is expected.

## [1.8.5]

### Added

- `bad-decisions doctor` checks a running deployment read-only: `/healthz` version, `/v1/packs` against the pack count, `/v1/round`, `/web/`, prefix-safe `/docs/` redirects, and, on the host, a pending activation request, unused staged files, and free disk space.
- `bad-decisions --version`.
- The release workflow installs the exact wheels it will publish into a clean venv and smoke-tests them (`scripts/check_installed.py`), and refuses a tag without a `## [X.Y.Z]` section in both changelogs.
- The rootless activator requires 1 GiB free under `APP_ROOT/releases` (`--min-free-mb`), and with `PACK_DIR` loads the live registry with the new release before switching to it. Rerun `bootstrap-rootless` to install it.
- The wheel installs `bad-decisions(1)` into `share/man/man1`, so `man bad-decisions` works after a `--user`, venv, or pipx install.

### Changed

- `deploy local` refuses to deploy when PyPI has a newer version than the installed command (`--allow-older` overrides), and the activator refuses to reinstall the version already serving (`--redeploy` overrides).
- `deploy local`, `rollback local`, and `pack replace-local` refuse a pending request before downloading or staging anything, warn about unused staged files, and on Ctrl-C after submitting explain that the activator will still finish the request.
- CI and release workflows use Node 24 action releases (checkout v7.0.1, setup-python v6.3.0, upload-artifact v7.0.1, download-artifact v8.0.1, which fails on artifact digest mismatches).
- `bad-decisions(1)` documents the service commands, `pack import` flags, local-deploy preconditions, every user-facing setting, and the configuration and release files. Releases now update the manual pages before tagging, and a test checks their version, commands, and settings.

### Fixed

- A `deploy local` that lost the race to another request, or was interrupted while staging, left its staging directory in `APP_ROOT/incoming`.
- The manual pages began with `\.TH` instead of `.TH`; formatters other than groff could lose the title header.

## [1.8.4]

### Added

- `bad-decisions pack replace-local OLD_ID URL --new-id NEW_ID` replaces one named pack in a rootless install's registry through the root activator (enabled by `bootstrap-rootless` with `PACK_DIR`): HTTPS download within CardDeck limits, full validation, a no-overwrite publish, a health and `/v1/packs` check, and exact restoration of the previous files on failure.

### Changed

- The web indexed-pack chooser selects Pretend You're Xyzzy imports by provenance (their source edition) instead of a `pyx-` id prefix, so renamed packs such as `furry` stay there, and each option shows its short pack id.
- Local management commands refuse to run from inside the deployed release (for example through a symlink into `APP_ROOT/current`) and explain how to install them separately with pipx; `EASY_DEPLOY.md` documents the migration.

## [1.8.3]

### Changed

- Peer Pressure streams cards from the loaded packs instead of copying every deck into each room: opening a room on a 6,000-card install drops from about 15 seconds to about 50 ms. Existing rooms are upgraded in place without repeating drawn cards.

### Fixed

- Peer Pressure tables no longer freeze when the Responsible Adult leaves or goes away: the role passes to the next connected player (whose own decision returns to their hand). The lobby host passes on too, so a departed room opener cannot block the start.
- Peer Pressure rooms are created atomically, and each player's view is built from one consistent snapshot.

## [1.8.2]

### Added

- `deploy local` warns when PyPI has a newer version than the installed command, and `deploy local`/`rollback local` print the activator's log on failure (no journal access needed).

### Fixed

- Trailing-slash URLs behind a path prefix redirect within the prefix: `/bad-decisions/docs/` went to `/docs` and 404'd.

## [1.8.1]

### Added

- `bad-decisions rollback local [RELEASE_ID]` rolls back a rootless install without sudo, with the same rules as `deploy.sh rollback`.
- `pip install --upgrade bad-decisions && bad-decisions deploy local` redeploys without a checkout: the wheel ships its `requirements.lock`, and `deploy local` downloads its own version's wheel from PyPI (SHA-256 checked) when no local build exists.

## [1.8.0]

Tagged but not published to PyPI; its changes first ship there in 1.8.1.

### Added

- Added Peer Pressure: ephemeral server-authoritative multiplayer with isolated SQLite rooms, private hands, anonymous decisions, Responsible Adult rotation, heartbeat synchronization, idempotent revisions, reconnection, and `regret together ROOM_ID`.

## [1.7.1]

### Fixed

- Made indexed-pack modal scrolling responsive by avoiding full-page backdrop repaints and isolating the scrolling list.

## [1.7.0]

### Added

- Indexed packs can now be selected in any combination from an accessible checkbox modal, with select-all, clear, staged apply/cancel, and keyboard dismissal controls.

## [1.6.4]

### Changed

- AWS API custom domains now support external DNS providers such as Cloudflare without requiring a Route 53 hosted zone; deployments emit and save the API Gateway CNAME target.

## [1.6.3]

### Fixed

- Bridged AWS CLI `aws login` sessions into local boto3 management operations using in-memory short-lived credential export; credentials are never printed or persisted by Bad Decisions.
- Added `bad-decisions pack seed-aws` so a deployment that completed before bundled-pack seeding failed can be repaired without rebuilding or redeploying.

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
