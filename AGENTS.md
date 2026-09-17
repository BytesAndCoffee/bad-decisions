# Cards Against Coffee contributor guide

> You are about to modify production infrastructure for a card game made from
> questionable decisions. Please make new questionable decisions deliberately.

This is an unofficial, unaffiliated fan project. It is absurd by design; its
validation, licenses, backups, and ZIP-bomb defenses are not.

## Project map

- `src/cah_engine/`: engine, pack models, CLI, API, and CAHPACK support.
- `src/cah_engine/data/packs/`: bundled, validated JSON packs.
- `client/`: separate dependency-free terminal-client distribution.
- `scripts/`: reproducible import/conversion utilities.
- `tests/`: pytest suite; keep behavioral and security coverage here.
- `deploy/` and `deploy.sh`: configurable systemd/nginx deployment tooling.

## Working rules

- Preserve the API's immutable-runtime model. Packs are loaded at startup; do
  not add API endpoints that import, upload, edit, or otherwise mutate packs.
- `CAH_PACK_DIR` replaces the bundled registry. Use `cah pack init-registry`
  before importing a portable pack when bundled packs should remain available.
- Keep CLI one-shot output clean: no banners, logs, or diagnostics on stdout.
- Maintain strict Pydantic validation and stable JSON error envelopes. Do not
  silently coerce malformed pack data.
- Changes to archive parsing require hostile-input tests. Reject traversal,
  symlinks, unexpected members, checksum failures, oversized content, and
  dangerous compression ratios before writing to disk.
- Do not weaken the non-overwrite and atomic-write behavior of pack imports.

## Content, attribution, and licensing

- Preserve card text, order, provenance, and licensing when migrating a source
  pack unless the task explicitly authorizes a content transformation.
- Do not add third-party card data without recorded source, license evidence,
  attribution, and redistribution compatibility.
- MAHA and owner-authorized Cards Against Coffee material are CC BY-SA 4.0.
  The bundled `base` pack remains CC BY-NC-SA 2.0; combined distributions must
  honor its more restrictive terms.
- Do not publish raw source-message corpora or personal data. The Coffee pack
  intentionally retains only compact source references.
- The project is an unofficial, unaffiliated fan project. Do not add Cards
  Against Humanity logos, trade dress, or claims of endorsement.

## Validation

Run before committing code changes:

```bash
.venv/bin/python -m pytest
PYTHONPATH=client/src .venv/bin/python -m pytest client/tests
bash -n deploy.sh
```

For a server release, build and validate the exact artifacts:

```bash
.venv/bin/python -m build .
.venv/bin/python -m build client
.pypi-venv/bin/twine check dist/cards_against_coffee_server-* client/dist/cards_against_coffee-*
```

## Deployment and publishing

- `deploy.sh` must remain portable: do not hard-code a host name, domain, user
  home, site file, or provider-specific network configuration.
- Require explicit configuration for an nginx site and public URL. Keep the
  upstream loopback-only unless a deployment task explicitly says otherwise.
- PyPI versions are immutable. Check the current published version and bump it
  before uploading; never rebuild different contents under an existing version.
- Never commit virtual environments, build artifacts, dotenv files, tokens, or
  generated local exports.
