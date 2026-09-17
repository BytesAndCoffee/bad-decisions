# Cards Against Coffee server

Python 3.12 engine, CLI, and FastAPI service for generating random completed
card rounds. Content is adult/offensive satire. This is not a multiplayer game
and has no accounts, database, uploads, runtime downloads, or mutable API state.

## PyPI packages

Cards Against Coffee is published as two packages:

- [`cards-against-coffee-server`](https://pypi.org/project/cards-against-coffee-server/)
  provides the engine, `cah` command, packs, and FastAPI application.
- [`cards-against-coffee`](https://pypi.org/project/cards-against-coffee/)
  provides the lightweight `coffee-cards` terminal client for the hosted API.

Install the server/runtime with:

```bash
python -m pip install cards-against-coffee-server
```

For the API-backed terminal client, install and run:

```bash
python -m pip install cards-against-coffee
coffee-cards
```

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

### Portable `.cahpack` archives

Share one pack as a `.cahpack` ZIP archive. Version 1 contains exactly
`manifest.json`, `pack.json`, `LICENSE.txt`, and `ATTRIBUTION.md`; the manifest
pins a SHA-256 checksum of the pack payload. Archive operations are local CLI
actions only—the HTTP API never imports or changes packs at runtime.

```bash
cah pack export maha ./maha.cahpack
cah pack validate ./maha.cahpack
cah pack import ./maha.cahpack /absolute/pack/registry
```

Import creates `<registry>/<pack-id>.json` atomically and refuses to overwrite
an existing pack. Configure that complete registry through `CAH_PACK_DIR` and
restart the service. The validator rejects malformed data, missing license or
attribution text, path traversal, symlinks, unexpected members, oversized
archives, high compression ratios, and checksum mismatches.

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

`deploy.sh` is a configurable Linux/systemd/nginx deployer. It creates immutable
wheel-based releases, manages a dedicated service account, and can add an nginx
location to a server block you explicitly identify. It never assumes a domain,
host name, user home, or existing site layout.

Choose the site configuration and add this marker inside its desired TLS
`server` block:

```bash
# cards-against-coffee-location
```

Then deploy, supplying the target site and its public base URL:

```bash
sudo NGINX_SITE_CONFIG=/etc/nginx/sites-available/example.com \
  PUBLIC_BASE_URL=https://example.com/cah \
  ./deploy.sh
```

The defaults are `APP_NAME=cards-against-coffee-server`, a `/cah` route, a
loopback listener on port 8000, and two workers. Override them through the
environment when needed; see `DEPLOYMENT.md` for every setting. The deployer
backs up only the files it changes, validates nginx before reloading it, tests
both local and public health endpoints, and restores the prior healthy release
if activation fails.
