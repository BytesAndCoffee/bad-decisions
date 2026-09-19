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
