- [x] Bridged AWS CLI login-session credentials into boto3 management calls and added a standalone AWS bundled-pack recovery command.
- [x] Completed the AWS-native path with private S3 runtime packs, CloudFront archives, Lambda indexing, DynamoDB Consequences, certificate-matched HTTPS, seeded bundled packs, and local management commands.
- [x] Reduced AWS fixed costs with one task, scale-to-two, one-AZ endpoints, and private compute retained.
- [x] Replaced the legacy CDK stack with a compatibility shim so the missing-`curl` ECS health-check failure cannot recur.

## 1.5.0

- Added the AWS hybrid deployment scaffold, Docker image, and local AWS setup/deploy command plumbing.

# Patch notes

Tracks fixes from the 2026-09-19 code review. Tick an item when its fix and test are done.

- [x] Keep indexed-pack modal scrolling on its own paint layer and avoid repainting a blurred full-page backdrop.
- [x] Add Peer Pressure multiplayer with isolated ephemeral room databases, privacy-safe projections, idempotent revisioned mutations, and Responsible Adult terminology.

## Should fix
- [x] 1. Catalog indexer (`deploy/object-archive/indexer/catalog.py`): add per-member size and compression-ratio limits; catch `zlib.error`/`RuntimeError`/`TypeError` per archive so one bad object can't 503 the catalog; align the 8 MiB limit with the 5 MiB client limit.
- [x] 2. Catalog endpoint: cache the payload with a TTL, and add nginx `proxy_cache` and `limit_req` to `nginx-carddeck-catalog.conf.template`.
- [x] 3. `deploy.sh:191`: add `--retry-delay 1 --retry-max-time 60` to the health-check curl so rollback isn't delayed by about 17 minutes.
- [x] 4. Remove the hard-coded `base` default (`deploy.sh:192` smoke test, `packs.py:86`, `cli.py:39`) so custom registries work.
- [x] 5. `archive.import_pack`: replace `exists()` + `os.replace()` with `os.link` (fail if exists); add `fsync`; make `import_index` all-or-nothing.
- [x] 6. `pyx_import.py`: `[0-7]{3}` octal check, handle 1-2 digit octal and `\xHH`, use `split("\n")` instead of `splitlines()`.

## Added on request
- [x] Manual rollback: `deploy.sh rollback [RELEASE_ID]` (`deploy/rollback.sh`), defaulting to the last good release from the `good-releases` watermark.
- [x] Consequences: optional SQLite request telemetry, durable round provenance, capability-protected Enjoy/Regret feedback, aggregate maintenance, and client integration.

## Review round 2 (code review of 98c1552)
- [x] R1. `rollback.sh`: `activate()` failures were ignored inside `if ... &&`, so a failed switch could report success.
- [x] R2. `rollback.sh`: watermark ordering could roll forward after an explicit rollback.
- [x] R3. Catalog: transient storage errors were cached as `rejected_archives`.
- [x] R4. Import: no-hard-link fallback wrote straight into the destination (not atomic).
- [x] R5. Catalog: lazy `Catalog()` hid bad config behind silent 503s.
- [x] R6. PYX: multi-byte UTF-8 octal/hex escapes decoded as mojibake.
- [x] R7. Catalog: `Cache-Control` advertised full TTL for stale bodies; TTL not validated.
- [x] R8. Catalog: `PayloadCache` lock/expiry timing and repeated cold-start scans.
- [x] R9. `import_index`: rollback missed a pack that failed after linking.
- [x] R10. All-packs default reviewed and deliberately kept (user decision).

## Should consider
- [x] Reject C0 control characters (except newline) in card text validators.
- [x] `operations.setup`: don't silently ignore `--pack-dir`; make config actually reach the service; honor `SERVICE_NAME` in `_service()`.
- [x] `.gitignore`: add `deploy/object-archive/secrets/` and `garage.toml`.
- [x] Verify or record `LICENSE.txt` / `ATTRIBUTION.md` against metadata in archives.

## Minor
- [x] Remove or wire up dead `remote_cli.py`.
- [x] Guard `signal.SIGPIPE` and make `Path.home()` lazy for Windows/odd environments.
- [x] Precompute pool resolution per selector (`packs.py:91-97`).
- [x] Make `initialize_registry` / `export_pack` / `export_all` clean up on failure.
- [x] Harden `client/cli.py` `_get_json` (size cap, non-dict error payload, `http.client` errors).
- [x] Tests: concurrent imports, catalog malformed archives, PYX escape edge cases.

## 2.0.0 plan

Decided 2026-09-27: unify on prompt/answer(s); `/v1` is removed (the owner is
its only consumer) and `/v2` replaces it; schema-v1 packs stay readable;
server-sent events are deferred to 2.1, but the v2 room API must allow them.

Rename (prompt/answer)
- [x] Pack schema v2 (`prompts`/`answers`); read v1 and v2, write v2.
- [x] Models, engine, pools, errors, CLIs (`--prompt-packs`/`--answer-packs`), PYX import output.
- [x] Consequences hashes byte-identical to 1.x (golden test over bundled packs).
- [x] Peer Pressure storage `prompt`/`answer` (room schema 4).
- [x] Catalog `prompt_count`/`answer_count` (indexer and AWS archive); activator reads v1 and v2.
- [x] Web UI: no "BLACK CARD" label or dark-prompt/light-answer pairing.

Packaging
- [x] `bad-decisions[aws]`, `[aws-deploy]`, and `[tui]` extras; the core install is 32 MB instead of 469 MB.

API v2
- [x] `/v2` routes with prompt/answer names; `/v1/*` returns 410 with an upgrade hint; `/healthz` stays unversioned.
- [x] One error envelope (Peer Pressure NACKs included) with `request_id`; meaningful status per code.
- [x] 422 responses do not echo request input.
- [x] Response models in OpenAPI for health, packs, rounds, feedback, and the error envelope on every route (Peer Pressure state stays documented in PEER_PRESSURE.md).
- [x] `ETag`/`Cache-Control` for packs; long-lived cache for versioned web assets.
- [x] Configurable CORS allowlist, off by default.
- [x] Neutral headers (`X-Client-ID`, `X-Session-ID`, `X-Feedback-Token`).
- [x] Peer Pressure: bearer token alone identifies the player; `POST .../end` replaces DELETE with a body.
- [x] Peer Pressure caps: live rooms, players per room, disk space before creating a room.
- [x] App-level rate limit for room creation, join, and feedback.

Cleanup
- [x] Remove `remote_cli.py`, `infra/aws/`, the `analytics` alias, and the client's `legacy_main`.
- [x] Removed plain `bad-decisions setup` (it wrote a config nothing read; deploy.sh configures Linux installs); `status`/`reload`/`stop --service NAME`.
- [x] Client: Python 3.10+, no `packaging` dependency (dependency-free again).
- [x] Reject C0 control characters (except tab and newline) and DEL in card text and printed metadata; all 47 production packs still validate.
- [x] A configured S3 pack bucket that yields nothing logs a warning instead of falling back silently (kept as a fallback: new AWS deployments start before `pack seed-aws`).
- [x] Remaining "Should consider" and "Minor" items above.

Review round 3 (full v2 review, 2026-09-27)
- [x] Removed-v1 contract also covers `/v1` itself plus HEAD and OPTIONS.
- [x] Peer Pressure rejects impossible draws before sampling instead of looping forever.
- [x] Room schema versions must match exactly; idempotency keys are scoped per authenticated player.
- [x] Exact CORS origins reject queries, fragments, credentials, wildcards, and malformed ports.
- [x] Catalog validates manifest layout, format, checksum, and pack identity before publishing metadata.
- [x] Catalog streaming bodies are closed; GET and HEAD share one response path.

Object store (owner request 2026-09-27)
- [x] Review deploy/object-archive: all 46 live archives validated with 2.0 (45 pass; xkcdb rebuilt to 1.2.1 for upload).
- [x] Indexer mirrors the importer's license-match and control-character checks (drift tests).
- [x] Indexer Dockerfile pins boto3 1.40.75.
- [x] Public archive nginx: GET/HEAD only, small body limit, per-IP rate limit.
- [x] Operator: snapshot live objects, upload all 46 audited schema-2 archives,
  redeploy the indexer, and verify the schema-2 catalog.

Release
- [x] Docs, man pages, changelogs, and a 1.x to 2.0 migration guide.
- [x] Prepare version 2.0.0 in package, web, and manual sources.
- [x] Merge, tag, publish, deploy (activator re-bootstrap if it changed), and update the Homebrew tap (2.0.0 and 2.0.1 live 2026-09-28).

## Log
<!-- date - item - what changed -->
2026-09-19 - 1 - Catalog indexer enforces importer-equivalent per-member size, ratio, symlink, encryption and total-size limits (5 MiB archive cap); any per-archive error goes to `rejected_archives` instead of a 503. Tests: `tests/test_catalog_indexer.py`.
2026-09-19 - 2 - Catalog payload cached in memory for `CATALOG_CACHE_TTL` (default 60s) with coalesced refresh and stale-on-error; nginx gets `proxy_cache` and `limit_req` (429) via new http-context template, documented in `deploy/object-archive/CATALOG.md`.
2026-09-19 - 3 - `deploy.sh` health-check curl has `--retry-delay 1 --retry-max-time 60`, so a failed deploy rolls back in about a minute. Test: `tests/test_deploy_script.py`.
2026-09-19 - 4 - Omitted `packs` now means all packs in the registry (`resolve_pools`, CLI, API `/v1/round` incl. the missed `api.py` site, `deploy.sh` smoke test, README); custom registries without `base` work.
2026-09-19 - 5 - `import_pack` publishes with `os.link` (no overwrite race), fsyncs file and directory, falls back to `O_EXCL` without hard links; `import_index` validates everything up front and rolls back on a failed install. Known limit: a crash mid-install can leave a partial set.
2026-09-19 - 6 - PYX COPY decoding handles 1-3 digit ASCII octal (rejects above `\377`) and `\xHH`; rows split on `\n` only, one trailing `\r` tolerated.
2026-09-19 - Minor tests - Added concurrent-import, malformed-catalog-archive and PYX escape edge-case tests (with items 1, 5, 6).
2026-09-19 - rollback - Added `deploy.sh rollback [RELEASE_ID]`: repoints `current` to the newest earlier (or named) release, restarts, health-checks, and restores the serving release if the target is unhealthy; rejects malformed, symlinked, incomplete and current IDs. Unknown `deploy.sh` arguments now error instead of starting a deploy. Tests: `tests/test_deploy_script.py`.
2026-09-19 - rollback watermark - `deploy.sh` records each release that passes all health checks in `$APP_ROOT/good-releases` (last 20, seeded from the serving release on first use); `rollback` defaults to the newest entry that is not current, so failed releases are never chosen, and drops the release it left so repeated rollbacks step back. Hosts without the file fall back to the newest older release with a warning. Tests: `tests/test_deploy_script.py`.
2026-09-19 - R1 - `rollback.sh`: every `activate()` step now returns 1 on failure (stale `current.new` fails loudly), a failed restore prints "RESTORE FAILED", `update_watermark` no longer `mv`s after a failed write; `deploy.sh` sends the `/v1/round` smoke-test output to `/dev/null`. Tests: `tests/test_deploy_script.py`.
2026-09-19 - R2 - `good-releases` is ID-sorted and ID-validated; a rollback to T keeps only entries `<= T`, so a plain rollback after `rollback A` refuses instead of rolling forward. `DEPLOYMENT.md` updated.
2026-09-19 - R3 - `catalog.py`: storage/network errors (`OSError`, botocore, sockets) fail the refresh so the stale catalog is served; `ARCHIVE_ERRORS` is content errors only, plus `EOFError`.
2026-09-19 - R4 - Import fallback for filesystems without hard links publishes the fsynced temp file under an `O_EXCL` `.<pack>.json.lock` plus `os.replace`; readers never see a partial pack, a leftover lock gives a distinct "stale lock" error (never broken by age). Residual: a crashed importer leaves the lock until removed by hand; non-importer writers are not coordinated.
2026-09-19 - R5 - `CATALOG_CACHE_TTL` validated once at startup (`cache_ttl()`, rejects non-numeric, negative, non-finite); `main()` validates config before binding and logs refresh failures.
2026-09-19 - R6 - PYX COPY octal/hex escapes are gathered into byte runs and decoded as strict UTF-8; invalid runs raise `PackConfigurationError` ("invalid UTF-8 in COPY byte escapes"); octal above `\377` still rejected deliberately.
2026-09-19 - R7 - `PayloadCache.get()` returns `(body, remaining_seconds)`; `/packs/index` sends `max-age=<remaining>` when fresh and `no-cache` for stale or under one second left.
2026-09-19 - R8 - Cache expiry measured after the build finishes; a cold-start failure is negative-cached for `min(ttl, 10s)`.
2026-09-19 - R9 - `import_index` tracks published packs by `(path, st_dev, st_ino)` via private `_import_pack(..., created=...)`; late failures roll back, and a pack another writer replaced is left alone.
2026-09-19 - R10 - Kept the all-packs default as decided. Follow-up: catalog.py changes need the object-archive container rebuilt separately; the rest needs a `deploy.sh` run.
2026-09-19 - docs - AGENTS.md now covers rollback/watermark, catalog indexer constraints, import atomicity/lock, PYX byte escapes, default-packs rule and update-install PORT gotcha; CLAUDE.md slimmed to Claude behavior plus local production notes and the approved screen-based sudo route.
2026-09-19 - docs - CLAUDE.md is now tracked; AGENTS.md and CLAUDE.md both carry equivalent conduct rules plus a 'Keeping agent guides in sync' rule (update both in the same change, no machine-specific details in tracked files); production details moved to Claude project memory. Test: tests/test_agent_guides.py.
2026-09-19 - docs - Parity across AI coding platforms: machine-specific production notes moved from Claude-only memory to git-ignored AGENTS.local.md referenced by both guides; both guides state that no rule may live only in one tool's private config. Tests: tests/test_agent_guides.py.
2026-09-19 - docs - Cross-platform parity: AGENTS.md is the single source of rules; CLAUDE.md, GEMINI.md, .cursor/rules/agents.mdc and .github/copilot-instructions.md are thin adapters with no rules; sync rule rewritten accordingly. Test: tests/test_agent_guides.py.
2026-09-19 - release - Bumped both packages and the web cache busters to 1.1.1 (server + client stay coordinated); added tests/test_release_versions.py so the four version sources and index.html cache busters cannot drift.
2026-09-19 - release - Added .github/workflows/ci.yml (server on 3.12, client on 3.9 and 3.13, deploy-script syntax) and release.yml (tag vX.Y.Z -> verify tag/main/versions + tests -> build and twine-check once -> PyPI Trusted Publishing behind the pypi environment), scripts/check_release_tag.py, RELEASING.md, and AGENTS.md release rules. Actions pinned to commit SHAs; only the publish job gets id-token. Tests: tests/test_release_workflows.py.
2026-09-19 - docs - Corrected the remote layout: both GitHub repos were public and the old cards-against-coffee repo is archived, so there is no private repo. Removed the archived remote, renamed bad-decisions-public to origin, and rewrote the AGENTS.md remote/push rules and RELEASING.md to match (old remote URL: https://github.com/BytesAndCoffee/cards-against-coffee.git).
2026-09-22 - consequences - Replaced the preliminary aggregate-only analytics scaffold with optional SQLite-backed request/round telemetry, deterministic content hashes, capability-protected feedback, atomic combination counters, private operator commands, and web/Regret client hooks. Tests: tests/test_analytics.py.
2026-09-22 - release - Prepared Consequences as release 1.1.2; server and client versions plus web cache busters remain coordinated.
2026-09-22 - web - Added the Minecraft-style “Now with Consequences!” masthead splash line.
2026-09-22 - release - Prepared the web splash update as 1.1.3 so deployed assets do not reuse the immutable 1.1.2 version.
2026-09-22 - agents - Clarified that an explicitly confirmed still-valid sudo ticket in the named deploy screen is acceptable; inspect-first and agreed-command safeguards remain mandatory.
2026-09-23 - licensing - Clarified the MIT software/card-content boundary, documented the official BytesAndCoffee service's non-commercial operation, identified BytesAndCoffee as package maintainer, and added a synthetic CardDeck metadata-preservation test.
2026-09-23 - release - Prepared the licensing and distribution-boundary documentation update as 1.1.4; server, client, and web cache busters remain coordinated.
2026-09-23 - client - Added `regret provenance` (text and JSON) and retained the represented packs' license, attribution, version, and source metadata in the secure last-draw cache.
2026-09-23 - release - Prepared the Regret provenance command as 1.1.5; server, client, and web cache busters remain coordinated.
2026-09-23 - operations - Added an explicit root preflight for `reload` and `stop`; read-only `status` remains unprivileged and all three paths have focused tests.
2026-09-23 - consequences - Made the `report` database path optional: use `BAD_DECISIONS_CONSEQUENCES_DB` first, then discover the installed release's persistent SQLite database; mutating commands still require an explicit path.
2026-09-23 - release - Prepared the operations privilege preflight and installed Consequences report default as 1.1.6; server, client, and web cache busters remain coordinated.
2026-09-23 - consequences - Fixed installed database discovery to use the virtual-environment prefix rather than resolving its Python symlink to the system interpreter.
2026-09-23 - release - Prepared the installed Consequences database discovery correction as 1.1.7; coordinated versions remain aligned.
2026-09-23 - consequences - Opened CLI reports with SQLite `mode=ro`, avoiding schema initialization so service-group readers do not need database write permission.
2026-09-23 - release - Prepared read-only installed Consequences reporting as 1.1.8; coordinated versions remain aligned.
2026-09-23 - docs - Added a README documentation index and corrected the release guide's example tag to use a version placeholder.

2026-09-23 - client - Added opt-in Consequences preferences, consent schema tracking, safe non-interactive defaults, and a cached API version check.

2026-09-23 - release - Prepared lockstep 1.2.0 versions and changed the client update check to use the configured API health version instead of PyPI.

2026-09-23 - incident - Documented a catastrophic breach of brand integrity: release engineering made a good decision. Root cause analysis ongoing; no recurrence expected.

2026-09-23 - release - Promoted the brand-integrity incident notes and GitHub changelog links into lockstep 1.2.0.

2026-09-24 - web - Added the first-visit Consequences disclaimer modal and browser-side opt-in gate; prepared lockstep 1.2.1.

2026-09-24 - web - Added a footer control to reopen the Consequences preference modal; prepared lockstep 1.2.2.

2026-09-24 - consequences - Added the interactive Textual report dashboard with dashboard, combination, prompt, and recent-draw drill-down views; prepared lockstep 1.3.0.

2026-09-24 - packaging - Made Textual a core server dependency so the Consequences dashboard requires no extra package; prepared lockstep 1.4.0.

2026-09-24 - docs - Added comprehensive bad-decisions(1) and regret(1) manual pages and included them in source distributions.

2026-09-24 - consequences - Fixed the TUI command to open the analytics database read-only instead of attempting schema writes.

2026-09-24 - consequences - Fixed duplicate prompt-hash row keys in the Textual dashboard by using composite provenance keys.

2026-09-24 - consequences - Added answer-card stats, sortable TUI tables, and opt-in local resolution of stored hashes to pack card values.

2026-09-24 - consequences - Fixed Textual 8 binding compatibility for the hash/value toggle.

2026-09-24 - release - Prepared lockstep 1.4.0 for private raw-text analytics, expanded TUI drill-downs, and documentation reorganization.

2026-09-24 - release - Fixed the release-workflow test path after moving long-form documentation into docs/; prepared lockstep 1.4.1.

2026-09-24 - aws - Split AWS deploy stages: setup aws is one-time and never rotates an existing token or discards saved state; deploy aws builds the image for the installed version, shows cdk diff, and asks before deploying (--yes for unattended); domain flags persist; new rotate-token aws rotates the secret and restarts ECS tasks. Tests: tests/test_aws.py.

2026-09-24 - release - Prepared lockstep 1.6.1 for the split AWS setup/deploy stages and rotate-token aws.

2026-09-24 - aws - Minimum-cost AWS stack: HTTP API + VPC link + Cloud Map replaces the ALB, public-subnet tasks replace interface endpoints, 0.25 vCPU/512 MiB Fargate Spot tasks (--capacity on-demand to opt out), API throttling, Python container health check, 30-day noncurrent S3 expiry, 10-image ECR retention, and bootstrap only when missing so setup aws works without IAM permissions. Tests: tests/test_aws.py (CLI and stack synthesis).

2026-09-24 - release - Prepared lockstep 1.6.2 for the minimum-cost AWS stack.

2026-09-24 - aws - Added external-DNS custom domains: Route 53 is optional and API Gateway emits a CNAME target for providers such as Cloudflare. Tests: tests/test_aws.py.

2026-09-24 - release - Prepared lockstep 1.6.4 for external-DNS AWS custom domains.
2026-09-26 - web - Replaced the single indexed-pack dropdown with an accessible checkbox modal that supports staged multi-pack selection, select-all, clear, apply, cancel, backdrop dismissal, and Escape dismissal.
2026-09-26 - deployment - Added a one-time privileged bootstrap and narrow systemd release activator so later version-matched local wheel deployments can run without sudo while preserving immutable releases, health-checked rollback, and the nginx/unit/secret privilege boundary.
2026-09-26 - deployment - Activator security review: result files are written O_EXCL|O_NOFOLLOW (a planted symlink in the group-writable inbox could make root write and chown any file); staged files are read through no-follow, non-blocking, size-capped descriptors and root copies the hashed bytes into a private directory the service user can read (it could not read the inbox, and pip could install different bytes than were hashed); dependencies come from the staged, pin-only requirements.lock; the freeze rejects hard links and is re-verified; SIGTERM/timeout still rolls back; the watermark is seeded like deploy.sh; the unit gains a 20-minute timeout and extra sandboxing. Tests: tests/test_rootless_deploy.py.
2026-09-26 - deployment - Rootless rollback and pip-only redeploys: the activator accepts an exact rollback request and applies rollback.sh's target selection, health-check restore, and watermark rules (tests/test_deploy_script.py runs every shared rollback scenario through both implementations); new `bad-decisions rollback local`; the wheel packages requirements.lock and `deploy local` falls back to this version's PyPI wheel verified against PyPI's SHA-256; rejected requests now carry their id so clients fail fast instead of timing out. Tests: tests/test_rootless_deploy.py, tests/test_deploy_script.py.
2026-09-26 - release - Prepared lockstep 1.8.1 for rootless rollback and pip-only local redeploys. The v1.8.0 publish was cancelled before PyPI; its changes ship as 1.8.1.
2026-09-26 - api - Trailing-slash redirects kept dropping root_path behind a prefix-stripping proxy (/bad-decisions/docs/ -> /docs, 404). Disabled Starlette's redirect_slashes; the 404 handler redirects to root_path + the slashless path only when that matches a route (never for //host paths). Tests: tests/test_cli_api.py.
2026-09-26 - deployment - The activator tees its output (including pip's) into activation/log.txt, published O_EXCL|O_NOFOLLOW like the result; local deploy/rollback print its tail on failure. deploy local warns when PyPI has a newer version than the installed command. Tests: tests/test_rootless_deploy.py.
2026-09-26 - release - Prepared lockstep 1.8.2 for prefix-safe slash redirects and rootless deployment diagnostics.
2026-09-26 - docs - Added docs/EASY_DEPLOY.md: sane defaults, first privileged install, one-time rootless bootstrap, pip-only deploy/rollback, troubleshooting, and a table of changes that still need the sudo deploy; linked from README, docs/README.md, and DEPLOYMENT.md.
2026-09-26 - peer-pressure - Review fixes: a departed Responsible Adult or lobby host no longer freezes the table (role passes to the next connected player in seat order; the successor's own decision is withdrawn to their hand; activity, away-marking, and repair happen in one _touch step that also recovers after everyone was away). Cards stream from the registry with a drawn_cards table instead of per-room deck copies inserted one autocommit at a time (~15 s -> ~50 ms per room on 6k cards), with an in-place upgrade for older rooms. Rooms are built under a temp name and os.link'ed into place; views are projected in one transaction; connections are closed. Tests: tests/test_peer_pressure.py.
2026-09-26 - release - Prepared lockstep 1.8.3 for the Peer Pressure departure fixes and streamed cards.
2026-09-26 - client - regret's HTTP error handler only understood the API error envelope, so Peer Pressure NACKs ({type, reason, message}) surfaced as a bare 'HTTP 409' and mutate() never saw 'stale_revision' to resync. It now reports 'reason: message' and adds hints for display_name_taken and game_in_progress. Tests: client/tests/test_together.py.
2026-09-26 - deployment - Rootless pack replacement: `pack replace-local` stages a fully validated canonical pack.json with an exact replace_pack request; the activator (with --pack-dir from bootstrap PACK_DIR) re-reads it no-follow, validates it with the release's schema as the service user, requires a plain old file declaring the old id and an unused new id, publishes by linkat, retires the old file by rename, restarts, checks /healthz and /v1/packs, and on any failure restores the same inode and removes only its own file. Management commands refuse to run from APP_ROOT/current or releases and point to pipx. Tests: tests/test_rootless_packs.py, tests/test_rootless_deploy.py.
2026-09-26 - release - Prepared lockstep 1.8.4 for rootless pack replacement, the management-install check, and regret's NACK messages.
2026-09-26 - web - Renaming a PYX pack to `furry` moved it out of the indexed chooser, which split packs on a `pyx-` id prefix. isIndexedPack() now keys on the importer's source edition ("Pretend You're Xyzzy SQL card set "), keeping the prefix as a fallback, and indexed options show the short id. Tests: tests/test_cli_api.py (runs the classifier with node).
2026-09-27 - packaging - Neither wheel shipped its manual page. Hatch shared-data now installs man/bad-decisions.1 and client/man/regret.1 (moved from man/ so the client sdist carries it) into share/man/man1; both pages also started with a stray `\.TH`. Tests: tests/test_manpages.py.
2026-09-27 - docs - Brought both manual pages up to 1.8.4: bad-decisions(1) gains the service commands (setup, serve, deploy, status, reload, stop), pack import flags, the local-deploy preconditions, missing runtime settings, and config/APP_ROOT files; regret(1) gains together --timeout and its prompt flow, the enable/disable aliases, and exit status 2. AGENTS.md and docs/RELEASING.md now require updating the pages before tagging. Tests: tests/test_manpages.py checks each page's .TH version, every CLI command, and every user-facing setting.
2026-09-27 - release - Release preflights: release.yml smoke-tests the exact built wheels in a clean venv (scripts/check_installed.py: --version, bundled packs, packaged lock, man pages, live serve) and check_release_tag.py requires a ## [X.Y.Z] changelog section; actions moved to Node 24 releases. Tests: tests/test_check_installed.py, tests/test_release_workflows.py.
2026-09-27 - deployment - Deploy preflights after the 1.8.4 rollout (a stale pipx index redeployed 1.8.3, Ctrl-C left a request running, and a refused second request leaked its staging directory): deploy local refuses a command older than PyPI (--allow-older) and a pending request before downloading, removes its stage on any failure unless the activator owns it, and explains that Ctrl-C does not cancel; the activator refuses the serving version (--redeploy), low disk (--min-free-mb, 1 GiB), and a release that cannot load the PACK_DIR registry, all before switching. New read-only `bad-decisions doctor` and `--version` on both CLIs. Tests: tests/test_rootless_deploy.py, tests/test_rootless_packs.py, tests/test_doctor.py.
2026-09-27 - release - Prepared lockstep 1.8.5: installed man pages, release and deploy preflights, `bad-decisions doctor`, and `--version` on both CLIs.
2026-09-27 - docs - Corrected the man page install note: pipx 1.4.3 already links a package's man pages into ~/.local/share/man (seen installing 1.8.5), so the "pipx 1.5 or newer" claim was wrong.
2026-09-27 - docs - `man regret` failed on a pyenv Mac: pip put the page under ~/.pyenv/versions/X/share/man, but man only searches next to PATH's bin directories, which pyenv replaces with shims. Both READMEs now explain where pages land, recommend pipx, and give the MANPATH line for pyenv/asdf. Tests: tests/test_manpages.py.
2026-09-27 - client - Installers cannot run post-install code, so pyenv users could not get `man regret` working without being told how. Added `regret doctor` (read-only; detects the install method and prints the MANPATH fix or a better install) and a Homebrew formula, homebrew/regret.rb, built, tested, and audited (--strict --new) with Linuxbrew from the 1.8.5 sdist. The client README, which is the PyPI description, now suggests Homebrew. Tests: client/tests/test_doctor.py, tests/test_homebrew_formula.py.
2026-09-27 - docs - Man page tags rendered glued ("--black-packsids", "deploylocal", "togetherroom"): alternating-font macros (.BI/.BR/.RI) join arguments without spaces. Rewrote 30 entries with explicit spacing or inline font escapes. Tests: tests/test_manpages.py rejects any alternating-macro line whose arguments would run together.
2026-09-27 - 2.0 - Cleanup and backlog: removed remote_cli.py, infra/aws, the analytics alias, legacy_main, and plain `setup`; systemctl commands take --service; control characters rejected in card text and metadata; archives must carry LICENSE.txt/ATTRIBUTION.md matching their metadata; exports and registry initialization remove only their own files on failure; PYX validation errors are PackConfigurationError; SIGPIPE guarded and home paths via expanduser; pools resolve once per selector; regret's HTTP helper caps responses and reports non-JSON bodies and dropped connections; client is dependency-free on Python 3.10+. Tests: tests/test_archive.py, test_models_engine.py, test_pyx_import.py, test_api_v2.py, test_extras.py, client/tests/test_cli.py.

2026-09-27 - Object store - Catalog indexing now enforces UTF-8 and exact metadata equality for license/attribution files plus importer-matched control-character rules; hostile schema-1/schema-2 coverage guards the boundary.
2026-09-27 - Object store edge - Public archive nginx now permits only GET/HEAD, caps request bodies at 1 MiB, and applies a documented 10r/s per-IP limit with a 30-request burst.
2026-09-27 - v2 review - Fixed complete /v1 retirement responses, impossible Peer Pressure draw hangs, cross-player idempotency-key collisions, future/older room schema acceptance, and permissive CORS-origin parsing; focused API and multiplayer regressions added.
2026-09-27 - Object store review - Catalog now verifies manifest structure, format, checksum, pack identity and nonempty legal documents, closes object streams, serves HEAD, and runs read-only with all capabilities dropped; both public hosts enforce GET/HEAD and body/rate limits.
2026-09-27 - xkcdb - Added scripts/rebuild_xkcdb.py and regression coverage; it reuses the hostile-tested CardDeck parser and reproduces the reviewed schema-2 xkcdb 1.2.1 archive byte-for-byte (sha256 f4cd71a43437bf9cebb460cecec8cc3efa34654ba7b45cba041c7be2463f82ba) without changing cards.
- Added an atomic, validated bulk rebuild tool for converting the complete CardDeck collection to pack schema 2 while preserving archive names and legal metadata.
2026-09-27 - object archive - Added a tracked 46-object source inventory and
auditable schema-2 rebuild manifest with source/selected/output hashes;
replacement archives must preserve pack identity, cards, and order.
2026-09-27 - object archive migration - Added a dry-run-by-default Garage
migration helper with preflight digest checks, per-object rollback copies,
post-upload verification, and automatic restoration tests.
2026-09-27 - object archive production - Migrated all 46 public archives to
schema 2 with verified Garage rollback objects, redeployed the hardened
catalog, verified schema 2 with zero rejections, downloaded/validated every
public archive, and completed a remote index import smoke test.
2026-09-27 - release docs - Synchronized CardDeck, API, Peer Pressure, deploy,
manual, migration, and changelog documentation and prepared lockstep 2.0.0
version metadata without publishing.
2026-09-28 - review - Claude review of v2 (Codex's pass included): regret together no longer dead-ends on a stale saved session (invalid_session, room_expired, room_not_found fall back to one fresh join); both public nginx hosts send X-Content-Type-Options nosniff; the indexer's metadata type check no longer relies on assert; Garage runs with no-new-privileges; ATTRIBUTION.md and AGENTS.md use prompt/answer and /v2. Operator follow-ups (nginx templates not yet installed, public rollback copies) are in HANDOFF.md. Tests: client/tests/test_together.py, tests/test_catalog_indexer.py.
2026-09-28 - release - 2.0.1: the go-live round-trip found regret 2.0.0 treating the 204 from DELETE feedback as non-JSON (1.x failed on it too, with a raw parse error); an empty success body is now {}. Tests: client/tests/test_cli.py.
2026-09-28 - postflight - Housekeeping scan after go-live: object-archive hardening verified live (writes 403, nosniff, downloads intact, catalog 2/46/0). The migration's rollback copies under rollback/carddeck-v2-20260927/ are publicly downloadable (including the pre-rebuild xkcdb); the archive nginx template now answers 404 for /rollback/ until the owner removes or moves them. The hand-made bad-decisions-pyx landing page and provenance-v1.json still advertise the 42 schema-1 checksums (none match) and omit furry; corrected index.html and provenance-v2.json were generated for upload. Tests: tests/test_catalog_indexer.py.
2026-09-28 - web - Reworked the complete orange accent system, including prompt and empty-state cards, around a darker burnt-sienna palette. Tests: tests/test_cli_api.py.
2026-09-28 - launch polish - Reordered the public front door around Regret's joke-first quick start; added navigable getting-started, Consequences, GitHub, release, demo, contributor, security, conduct, issue, and pull-request guidance; added a tested minimal CardDeck example, complete package discovery metadata, and a polished social-preview SVG. Warmed burnt sienna to a more orange, AA-contrast accent and intentionally omitted the unrelated legacy slogan. Tests: tests/test_public_polish.py, tests/test_cli_api.py.
2026-09-28 - packaging - Pointed the repository Homebrew formula at the immutable 2.0.2 client sdist and added current Ruby typing/frozen-string headers.
2026-09-28 - release - Prepared lockstep 2.0.3 for the public-launch polish, CardDeck creator path, package metadata, community health, and warmer browser palette.
2026-09-28 - launch polish - Finalized the warmer accessible orange palette, deterministic social-preview rendering, concise star CTA, Discussions guidance, maturity rationale, link/version cleanup, and Homebrew readiness notes; retained the explicit LICENSE content boundary over SPDX badge detection. Tests: tests/test_public_polish.py, tests/test_cli_api.py.
2026-09-29 - docs - Embedded the recorded Regret terminal demo on the repository front page instead of leaving the GIF discoverable only under docs/assets. Tests: tests/test_public_polish.py.
