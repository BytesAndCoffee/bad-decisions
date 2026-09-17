# Multi-pack card engine

Python 3.12 engine, CLI, and FastAPI service for generating random completed
card rounds. Content is adult/offensive satire. This is not a multiplayer game
and has no accounts, database, uploads, runtime downloads, or mutable API state.

The immutable registry loads and validates once at startup. `base` is the
default; `--packs maha` restores the original MAHA-only behavior. Black and
white selectors are independent and use the same resolver in the CLI/API.

## Install and run

```bash
/usr/bin/python3.12 -m venv .venv
.venv/bin/pip install --requirement requirements-dev.lock
.venv/bin/pip install --no-deps --editable .
.venv/bin/pytest
.venv/bin/cah --list-packs
.venv/bin/cah --oneshot
.venv/bin/uvicorn cah_engine.api:create_app --factory --host 127.0.0.1 --port 8000
```

Examples: `cah --packs base,maha --oneshot`, `cah --black-packs maha
--white-packs base,maha --oneshot`, `cah --packs all --black-packs maha
--oneshot`, and `cah --black-packs base --white-packs maha --rapid --delay
0.5`. `--oneshot` writes only the result. Interactive mode requires a TTY.
Rapid mode prints immediately, waits between rounds, and flushes output.

Selectors are case-sensitive comma-separated IDs. Surrounding whitespace is
trimmed; repeats preserve first occurrence without duplicating cards. `all`
must appear alone and expands in sorted registry order. `black_packs` and
`white_packs` override only their own side. Every supplied selector is still
validated. Empty values/elements, unknown IDs, empty resolved pools, and white
capacity below the maximum selected slot count are errors. Sampling is uniform
by card identity; answers are sampled without replacement within a round.

## Packs and import

JSON schema version 1 is illustrated by the bundled packs. IDs are lowercase
slugs; identities are `(pack,id)`. Templates permit only anonymous `{}` fields
and escaped `{{`/`}}`; slots are explicit positive integers. One-color packs
are valid, but a pack cannot be entirely empty. Duplicate text remains intact.

An absolute `CAH_PACK_DIR` replaces (never merges with) bundled packs. Adding a
pack requires validation and restart:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_packs.py /absolute/pack/dir
```

Reproduce the pinned imports (Poppler is required):

```bash
python3 scripts/import_base.py CAH_MainGame.pdf src/cah_engine/data/packs/base.json --report imports/base-validation.json
python3 scripts/import_maha.py imports/original_cah_maha.py src/cah_engine/data/packs/maha.json --report imports/maha-migration.json
```

The official 2019-12-02 PDF produces 80 black/500 white cards. MAHA produces
27 black/52 white cards with canonical content digest
`2d13f1b5f5f4e51f5ec57186d34e5ab9cea7d703cda7766ea1ad4941a1b8c3fd`.
See `ATTRIBUTION.md` and `imports/manifest.json`. Base content is CC BY-NC-SA
2.0 and noncommercial. MAHA is owner-authorized project content created with
ChatGPT assistance and is CC BY-SA 4.0. When distributing combined content,
honor the more restrictive applicable base-pack CC BY-NC-SA 2.0 terms as well.

## HTTP API

```bash
curl --fail-with-body http://127.0.0.1:8000/healthz
curl --fail-with-body http://127.0.0.1:8000/v1/packs
curl --fail-with-body http://127.0.0.1:8000/v1/packs/maha
curl --fail-with-body http://127.0.0.1:8000/v1/round
curl --fail-with-body --get http://127.0.0.1:8000/v1/round --data-urlencode 'packs=base,maha'
curl --fail-with-body --get http://127.0.0.1:8000/v1/round --data-urlencode 'black_packs=maha' --data-urlencode 'white_packs=base,maha'
curl --fail-with-body --get http://127.0.0.1:8000/v1/round --data-urlencode 'black_packs=base' --data-urlencode 'white_packs=maha'
curl --fail-with-body --get http://127.0.0.1:8000/v1/round --data-urlencode 'packs=all'
curl -i 'http://127.0.0.1:8000/v1/round?black_packs=does-not-exist'
```

`/docs` and `/openapi.json` are enabled. Round responses are `no-store` and
include selected pools and represented-pack provenance. Unknown/repeated query
keys and selector errors return a stable JSON error envelope. Request logs
include method, path, status, duration, and bounded/generated request ID, never
card bodies. CORS is disabled. Nginx-generated errors may use nginx formatting.
The deployed `/cah/` endpoint returns a compact plain-text how-to with the
available API paths and filter examples.

## Production deployment and rollback

On dev-vps, after the local tests have passed, run the simple release deployer:

```bash
sudo ./deploy.sh
```

It builds the wheel, creates `/opt/cah-api/releases/<UTC timestamp>`, installs
the exact runtime lockfile and wheel, atomically switches `/opt/cah-api/current`,
backs up a pre-existing unit/environment file, installs/enables `cah-api`, and
verifies health plus an independently filtered round. It configures the existing
`bytes.coffee` nginx TLS site at `/cah/`, validates and reloads nginx, and tests
the routed HTTPS health endpoint. The MAHA route is publicly served with the
project owner's authorization and its CC BY-SA 4.0 attribution/share-alike notice.
Release files are owned by `root:cah-api`; the service group receives read and
execute permissions only, never write permissions.

Install `python3.12-venv`, build tooling, nginx, and Poppler only for imports.
Create a locked `cah-api` system user. Build a wheel, create immutable
`/opt/cah-api/releases/<release-id>`, create its venv with
`/usr/bin/python3.12`, install runtime locks, then `pip install --no-deps` the
wheel. Make the release root/deployer-owned and non-writable to `cah-api`.
Install `/etc/cah-api.env` before the unit; install the unit, run
`systemctl daemon-reload`, then enable/start it. Two workers are a modest
default; use a systemd override with an edited full `ExecStart` for one worker
on a small VPS. Host/port are command arguments, not environment settings.

Before changing nginx: inspect `nginx -T`, listeners, server names, TLS, auth,
and upstream conventions; back up only the affected file. Integrate the route
without replacing existing sites. Run `nginx -t` before graceful reload. Use
the existing ACME/certificate workflow and redirect HTTP to HTTPS for public
access. Keep port 8000 localhost-only; without an established domain use SSH
forwarding. Check `systemctl status cah-api`, `journalctl -u cah-api`, localhost
health, real generation, the external route, and a controlled service restart.

For updates, stage and validate a new release, smoke-test on a spare localhost
port, atomically repoint `/opt/cah-api/current`, restart, and verify. Keep the
prior release plus env/nginx backups. Roll back by repointing `current` to the
previous release, restoring only changed configuration if needed, restarting,
and rechecking health. A normal service restart can briefly interrupt traffic;
this setup does not claim zero downtime.
