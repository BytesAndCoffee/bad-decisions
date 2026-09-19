# Bad Decisions contributor guide

> You are about to modify production infrastructure for a card game made from
> questionable decisions. Please make new questionable decisions deliberately.

This is an unofficial, unaffiliated fan project. It is absurd by design; its
validation, licenses, backups, and ZIP-bomb defenses are not.

## Project map

- `src/bad_decisions/`: engine, pack models, CLI, API, and CardDeck support.
- `src/bad_decisions/data/packs/`: bundled, validated JSON packs.
- `client/`: separate dependency-free terminal-client distribution.
- `scripts/`: reproducible import/conversion utilities.
- `tests/`: pytest suite; keep behavioral and security coverage here.
- `deploy/` and `deploy.sh`: configurable systemd/nginx deployment tooling;
  `deploy/rollback.sh` implements `deploy.sh rollback`.
- `deploy/object-archive/`: Garage object store, nginx templates, and the
  standalone catalog indexer (`indexer/catalog.py`).
- `patchnotes.md`: checklist and log of review fixes; tick the item and add a
  one-line note for every notable change.
- Agent adapters (`CLAUDE.md`, `GEMINI.md`, `.cursor/rules/agents.mdc`,
  `.github/copilot-instructions.md`): thin pointers to this file; no rules.

## Working rules

- Add a test with every fix. Archive, import, and catalog changes need
  hostile-input tests.
- Preserve the API's immutable-runtime model. Packs are loaded at startup; do
  not add API endpoints that import, upload, edit, or otherwise mutate packs.
- `BAD_DECISIONS_PACK_DIR` replaces the bundled registry. Use `bad-decisions pack init-registry`
  before importing a portable pack when bundled packs should remain available.
- An omitted `packs` selector means every pack in the loaded registry (API
  `/v1/round`, CLI `--packs`, deploy smoke test). Never hard-code a default pack
  id: custom registries may not contain `base`.
- Keep CLI one-shot output clean: no banners, logs, or diagnostics on stdout.
- Maintain strict Pydantic validation and stable JSON error envelopes. Do not
  silently coerce malformed pack data.
- Changes to archive parsing require hostile-input tests. Reject traversal,
  symlinks, unexpected members, checksum failures, oversized content, and
  dangerous compression ratios before writing to disk.
- Do not weaken the non-overwrite and atomic-write behavior of pack imports.
  `import_pack` publishes with `os.link` (fails if the pack exists) after
  fsyncing a temp file. Where hard links are unsupported it falls back to an
  `O_EXCL` lock file (`.<pack>.json.lock`) plus `os.replace`. A lock left by a
  crashed importer is reported, never broken by age; a human removes it.
  `import_index` downloads and validates everything before installing and rolls
  back only files it created (matched by path, device, and inode).
- PYX COPY escapes: octal and hex escapes are raw bytes. Consecutive ones are
  decoded as strict UTF-8 (invalid runs are a `PackConfigurationError`), and
  octal above `\377` is rejected on purpose rather than wrapped.
- The catalog indexer is a standalone container image that copies only
  `catalog.py`: it must not import `bad_decisions`, and it imports `boto3`
  lazily. Its size/ratio limits are duplicated from `archive.py` and guarded by
  a drift test. Only errors caused by an archive's own contents go to
  `rejected_archives`; storage and network errors must propagate so the last
  good catalog is served. Validate configuration at startup, not per request.

## Agent conduct

- Use the repo venv: `.venv/bin/python`. Never install into the system Python.
- Do not run `deploy.sh`, `bad-decisions deploy`, `twine upload`, or `git push`
  unless the user asks. Leave changes uncommitted unless asked to commit.
- Push only to the private `origin`. Never push to the public remote
  (`bad-decisions-public`) without being asked.
- Privileged (`sudo`) steps belong to the human. Agents have no sudo, and sudo
  tickets are per terminal, so `sudo -v` elsewhere does not reach an agent's
  shell. Do not work around this (no sudoers edits, no cached-ticket tricks).
  The only approved route: the user starts `screen -S deploy`, runs `sudo -v` in
  it, and says so in that turn. Then read the window first
  (`screen -S deploy -X hardcopy <file>`), send only the agreed command with
  `screen -S deploy -X stuff`, watch the output, and verify from outside
  afterward. Type nothing else into that window.
- Never read or print secrets such as service env files.
- Machine-specific facts (host names, ports, site files, production paths) stay
  out of tracked files. They live in the git-ignored `AGENTS.local.md` at the
  repo root; read it at the start of any session that touches deployment, if it
  exists. Do not keep them only in one tool's private memory or config.
- Review tools can report unreliable line numbers; re-read the code before
  fixing. When parallelizing work, give each agent a disjoint file set and keep
  `patchnotes.md` and the final full test run for one coordinator.

## Keeping agent guides in sync

People should get the same experience with any AI coding platform, so this file
is the single source of truth for rules and project facts. Never keep a rule
only in one tool's private config, memory, or settings.

- Add, change, or remove rules here only. The adapters listed in the project map
  contain no rules of their own; they just point to this file (and to
  `AGENTS.local.md`). If an agent finds a rule in an adapter, move it here.
- Supporting a new platform means adding a thin adapter and listing it in
  `tests/test_agent_guides.py`, not copying rules into it.
- `tests/test_agent_guides.py` enforces this: every adapter references this
  file, stays short, and carries no rules or machine-specific details.

## Content, attribution, and licensing

- Preserve card text, order, provenance, and licensing when migrating a source
  pack unless the task explicitly authorizes a content transformation.
- Do not add third-party card data without recorded source, license evidence,
  attribution, and redistribution compatibility.
- MAHA and owner-authorized custom material are CC BY-SA 4.0.
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
bash -n deploy/rollback.sh
```

Deployment scripts are tested without root: `tests/test_deploy_script.py` runs
`deploy/rollback.sh` in a temp `APP_ROOT` with stub `systemctl` and `curl`, and
checks `deploy.sh` statically. Tests must never run the real `deploy.sh`.

For a server release, build and validate the exact artifacts:

```bash
.venv/bin/python -m build .
.venv/bin/python -m build client
.pypi-venv/bin/twine check dist/bad_decisions-* client/dist/bad_decisions_client-*
```

## Deployment and publishing

- `deploy.sh` must remain portable: do not hard-code a host name, domain, user
  home, site file, or provider-specific network configuration.
- Require explicit configuration for an nginx site and public URL. Keep the
  upstream loopback-only unless a deployment task explicitly says otherwise.
- Updating an existing install: use `CONFIGURE_NGINX=0` and set `PORT` to the
  running service's port (the default is 8000). A wrong `PORT` makes the health
  check probe the wrong service, so the deploy fails and rolls itself back.
- Releases are immutable directories under `$APP_ROOT/releases/<UTC id>`. A
  failed deploy restores config backups and the previous release automatically.
- `sudo ./deploy.sh rollback [RELEASE_ID]` (root only) repoints `current`,
  restarts, and waits for `/healthz`; if the target is unhealthy it restores
  the release that was serving. It changes only the release, not env, unit, or
  nginx files (backups are in `$APP_ROOT/backups/`). Unknown `deploy.sh`
  arguments are an error, so a typo cannot start a deploy.
- `$APP_ROOT/good-releases` is the rollback watermark: release IDs, oldest
  first, appended only after every deploy health check passes (last 20; seeded
  from the serving release on first use). The default rollback target is the
  newest listed release that is not current. A rollback to T keeps only entries
  `<= T`, so repeating it steps back and never rolls forward. Hosts without the
  file fall back to the newest older release with a warning.
- The object-archive catalog service is deployed separately with
  `deploy/object-archive/docker-compose.catalog-v3.yml`, not by `deploy.sh`.
- PyPI versions are immutable. Check the current published version and bump it
  before uploading; never rebuild different contents under an existing version.
- Never commit virtual environments, build artifacts, dotenv files, tokens, or
  generated local exports.
