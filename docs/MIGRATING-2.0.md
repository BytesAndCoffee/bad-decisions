# Migrating to Bad Decisions 2.0

Bad Decisions 2.0 removes the version-1 HTTP API and standardizes project
language on prompts and answers. `/healthz`, the browser UI, and documentation
endpoints remain unversioned.

## API clients

Replace `/v1` with `/v2`. Version-1 paths return HTTP 410 with an upgrade
hint; they are not aliases. Round payloads use `prompt`, `answers`, and
`selection.prompt_packs`/`selection.answer_packs`. Use `X-Client-ID` and
`X-Session-ID` for opted-in Consequences telemetry and `X-Feedback-Token`
for feedback capabilities.

Upgrade Regret with `brew upgrade regret` or
`pipx upgrade bad-decisions-client`. The 2.0 client requires Python 3.10 or
newer.

## CLI selectors

`--packs` still selects both pools. Rename `--black-packs` to
`--prompt-packs` and `--white-packs` to `--answer-packs`.

## Pack payloads and catalogs

CardDeck remains archive format version 1, but newly written `pack.json`
payloads use schema 2: `prompts` replaces `black`, `answers` replaces
`white`, and prompt `text` replaces `repr`. Schema-1 packs remain readable
and are upgraded in memory. Exports and rebuilt archives always write schema 2.

Catalog schema 2 reports `prompt_count` and `answer_count`. Importers accept
catalog schema 1 or 2 during migration. Archive `LICENSE.txt` and
`ATTRIBUTION.md` must exactly match the corresponding metadata fields, and
printed metadata/card text rejects unsafe control characters.

## Peer Pressure

Peer Pressure moved to `/v2/peer-pressure/rooms`. The bearer session token
alone identifies a player. Rejoining sends the saved token to the join route;
mutations include a UUID request ID and expected revision. Heartbeats return
`revision` and `resync`. Rooms can be explicitly ended. Errors use the
standard JSON error envelope.

## Installation extras

The core server remains `pip install bad-decisions`. The Textual analytics
dashboard is included in core. AWS management requires
`pipx install 'bad-decisions[aws-deploy]'`.

## Self-hosted deployment

Before the first 2.0 rootless deployment, reinstall the privileged activator
from the checkout so it understands schema 2 and `/v2/packs`:

```bash
sudo ./deploy.sh bootstrap-rootless
```

Then upgrade the independent management installation and deploy normally. Do
not run management commands through a symlink into the serving release.
