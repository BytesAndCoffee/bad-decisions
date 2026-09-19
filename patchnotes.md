# Patch notes

Tracks fixes from the 2026-09-19 code review. Tick an item when its fix and test are done.

## Should fix
- [x] 1. Catalog indexer (`deploy/object-archive/indexer/catalog.py`): add per-member size and compression-ratio limits; catch `zlib.error`/`RuntimeError`/`TypeError` per archive so one bad object can't 503 the catalog; align the 8 MiB limit with the 5 MiB client limit.
- [x] 2. Catalog endpoint: cache the payload with a TTL, and add nginx `proxy_cache` and `limit_req` to `nginx-carddeck-catalog.conf.template`.
- [x] 3. `deploy.sh:191`: add `--retry-delay 1 --retry-max-time 60` to the health-check curl so rollback isn't delayed by about 17 minutes.
- [x] 4. Remove the hard-coded `base` default (`deploy.sh:192` smoke test, `packs.py:86`, `cli.py:39`) so custom registries work.
- [x] 5. `archive.import_pack`: replace `exists()` + `os.replace()` with `os.link` (fail if exists); add `fsync`; make `import_index` all-or-nothing.
- [x] 6. `pyx_import.py`: `[0-7]{3}` octal check, handle 1-2 digit octal and `\xHH`, use `split("\n")` instead of `splitlines()`.

## Added on request
- [x] Manual rollback: `deploy.sh rollback [RELEASE_ID]` (`deploy/rollback.sh`), defaulting to the last good release from the `good-releases` watermark.

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
- [ ] Reject C0 control characters (except newline) in card text validators.
- [ ] `operations.setup`: don't silently ignore `--pack-dir`; make config actually reach the service; honor `SERVICE_NAME` in `_service()`.
- [ ] `.gitignore`: add `deploy/object-archive/secrets/` and `garage.toml`.
- [ ] Verify or record `LICENSE.txt` / `ATTRIBUTION.md` against metadata in archives.

## Minor
- [ ] Remove or wire up dead `remote_cli.py`.
- [ ] Guard `signal.SIGPIPE` and make `Path.home()` lazy for Windows/odd environments.
- [ ] Precompute pool resolution per selector (`packs.py:91-97`).
- [ ] Make `initialize_registry` / `export_pack` / `export_all` clean up on failure.
- [ ] Harden `client/cli.py` `_get_json` (size cap, non-dict error payload, `http.client` errors).
- [x] Tests: concurrent imports, catalog malformed archives, PYX escape edge cases.

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
