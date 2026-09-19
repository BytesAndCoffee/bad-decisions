# Bad Decisions

**Bad Decisions** is a reusable engine and service for fill-in-the-blank party
card games. It provides a validated pack registry, a stateless REST API, a
browser client, a terminal client, and CardDeck portable-pack archives.

The engine makes no network requests while serving a hand and never changes
packs through the HTTP API. Prompt and response selectors are independent; the
registry is loaded at startup and can be run with multiple workers.

## Install

```bash
python -m pip install bad-decisions
bad-decisions --help
bad-decisions --oneshot
```

The cross-platform client is separate:

```bash
python -m pip install bad-decisions-client
regret health
regret deal
```

The configured hosted browser client is at
[`/bad-decisions/web/`](https://bytes.coffee/bad-decisions/web/).

## Packs

Set `BAD_DECISIONS_PACK_DIR` to an absolute registry directory. It replaces
the bundled registry and is loaded only at startup.

```bash
bad-decisions pack validate example.carddeck
bad-decisions pack init-registry /absolute/pack/registry
bad-decisions pack import example.carddeck /absolute/pack/registry
```

[CardDeck 1](CARDDECK.md) defines the portable format. Its ZIP validation
rejects traversal, symlinks, unexpected members, checksum mismatches,
oversized content, and dangerous compression ratios before any pack is written.

### Public CardDeck catalog and remote imports

The live [public CardDeck catalog](https://bad-decisions.objects.us-west-1.bytes.coffee/packs/index)
lists every intentionally public archive with metadata, licensing/provenance,
SHA-256, and direct download URLs.

The runtime remains immutable: it loads packs at startup and has no endpoint to
upload or alter them. To add a public pack, use the explicit remote-import CLI,
then restart with `BAD_DECISIONS_PACK_DIR` pointing to the registry:

```bash
bad-decisions pack import --remote \
  https://bad-decisions-native.objects.us-west-1.bytes.coffee/packs/coffee.carddeck \
  /absolute/pack/registry

bad-decisions pack import --index \
  https://bad-decisions.objects.us-west-1.bytes.coffee/packs/index \
  /absolute/pack/registry \
  --pack coffee \
  --pack pyx-2-base-game-us
```

Remote imports accept HTTPS only and reject redirects, URL credentials,
oversized responses, malformed catalogs, duplicate selections, and invalid
archives before the normal atomic, non-overwrite import occurs.

### Pretend You're Xyzzy imports

The distribution does not bundle Pretend You're Xyzzy card data. If you have a
lawfully acquired `cah_cards.sql` dump, the separate importer emits one
attributed `.carddeck` archive per active card set and records the supplied
source URL and SHA-256. Those generated packs are CC BY-NC-SA 3.0 and must stay
non-commercial and share-alike.

```bash
PYTHONPATH=src .venv/bin/python scripts/import_pyx.py /path/to/cah_cards.sql ./pyx-carddecks \
  --source-url 'https://raw.githubusercontent.com/ajanata/PretendYoureXyzzy/<commit>/cah_cards.sql' \
  --retrieved 2026-09-17
```

Bundled content retains its own provenance and licensing. The `coffee` pack is
owner-authorized material based on IRC messages, distributed under CC BY-SA
4.0; its raw source-message corpus is not included. The `base` pack is licensed
CC BY-NC-SA 2.0; operators distributing it must honor those terms.

## API

```bash
uvicorn bad_decisions.api:create_app --factory --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/packs
curl http://127.0.0.1:8000/v1/round
curl 'http://127.0.0.1:8000/v1/round?packs=maha'
```

Without a `packs` parameter (or `--packs` in the CLI), rounds draw from every pack
in the loaded registry, so custom `BAD_DECISIONS_PACK_DIR` registries need no `base`.

`/docs` exposes OpenAPI documentation. Errors use a stable JSON envelope and
request responses include a request ID.

## Linux deployment

Bad Decisions is Linux-native for production deployment:

```bash
bad-decisions setup
sudo NGINX_SITE_CONFIG=/etc/nginx/sites-available/example.com \
  PUBLIC_BASE_URL=https://example.com/bad-decisions \
  ./deploy.sh
```

The deployer creates an unprivileged service account, immutable wheel releases,
a `bad-decisions.service` unit, and optional loopback nginx proxy configuration.
See [DEPLOYMENT.md](DEPLOYMENT.md).

## Development

```bash
.venv/bin/python -m pytest
PYTHONPATH=client/src .venv/bin/python -m pytest client/tests
bash -n deploy.sh
.venv/bin/python -m build .
.venv/bin/python -m build client
```
