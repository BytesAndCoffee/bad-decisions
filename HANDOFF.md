# 2.0.0 handoff (branch `v2`; delete this file before merging to main)

Read AGENTS.md first, then this file. The plan and its checkboxes live in
patchnotes.md under "2.0.0 plan"; tick items there as they land. Each stage is
one commit on `v2`. Validate before every commit:
`.venv/bin/python -m pytest` (536 pass at 8d1c2e5) and
`PYTHONPATH=client/src .venv/bin/python -m pytest client/tests` (38 pass),
plus `bash -n deploy.sh deploy/rollback.sh`.

## Decisions (owner, 2026-09-27)
- Release title: "Bad Decisions 2.0: Terrible choices at terrifying speeds"
  (CHANGELOG heading, GitHub release name, tag message).
- Unify on prompt/answer(s). Card IDs already in packs stay as they are.
- `/v1` API is removed entirely (owner is the only consumer); `/v2` replaces it.
  `/v1/*` returns 410 with an upgrade hint; `/healthz` stays unversioned.
- Schema-1 packs stay readable; everything written is schema 2.
- Server-sent events are deferred to 2.1; the v2 room API already allows them.
- Everything ships as 2.0.0; there is no 1.8.6.
- Archives must carry LICENSE.txt/ATTRIBUTION.md that exactly match the pack's
  license_notice/attribution (strict), and xkcdb gets rebuilt to comply (done,
  see below).

## Status
- [x] Stage 1: prompt/answer rename, pack schema 2 (a4b1fd1).
- [x] Stage 2: `[aws]` (boto3), `[aws-deploy]` (+CDK), `[tui]` extras (dbefb62).
- [x] Stage 3: `/v2` API; all clients moved to v2 (3b5ab58).
- [x] Stage 4: dead code and the whole review backlog (8d1c2e5).
- [ ] Stage 4b: object store (reviewed; findings below, fixes not yet made).
- [ ] Stage 5: docs, versions, release, deploy, Homebrew.

## Stage 4b: object store (deploy/object-archive)
Audit done: all 46 live catalog archives downloaded and validated with 2.0
code. 45 pass. All are pack schema 1 (2.0 reads them; no re-export needed).
The live catalog is still schema 1 (1.x indexer).

1. xkcdb (owner chose strict + rebuild): rebuilt archive is at
   `build/xkcdb/xkcdb.carddeck` (git-ignored; sha256
   f4cd71a43437bf9cebb460cecec8cc3efa34654ba7b45cba041c7be2463f82ba). Version
   1.2.1, schema 2, cards identical; license_notice/attribution now hold the
   full LICENSE/ATTRIBUTION text worded as prompts/answers. The OWNER uploads
   it to bucket `bad-decisions-native`, key `packs/xkcdb.carddeck` (uploader
   credential; agents have none). If `build/` was cleaned, regenerate it: the
   exact transformation is in the session log / redo by hand from the live
   archive (copy LICENSE.txt and ATTRIBUTION.md into the metadata, reword
   white/black to answers/prompts, version 1.2.1, add a modifications line).
2. Indexer drift (fix in `deploy/object-archive/indexer/catalog.py`, standalone:
   no bad_decisions import; add drift tests in tests/test_catalog_indexer.py):
   the catalog lists archives the 2.0 importer rejects. Mirror, as
   rejected_archives content errors: (a) LICENSE.txt/ATTRIBUTION.md must be
   UTF-8 and equal metadata license_notice/attribution after strip(); (b) card
   text/template and printed metadata must not contain C0 controls except
   tab/newline, or DEL (same regex as models._CONTROL). read_members must then
   also read those two members (bounded like the others).
3. `indexer/Dockerfile.catalog`: pin boto3==1.40.75 (matches requirements-extras.lock;
   it says 1.35.99).
4. `nginx-object-archive.conf.template` (public, read-only s3_web): replace
   `client_max_body_size 0;` with a small limit, add `limit_except GET HEAD {
   deny all; }`, and a per-IP `limit_req` zone (define it in the http-context
   template like the catalog's) to cap download abuse. Update README.md.
5. Operator step after the server release: rebuild/redeploy the indexer
   (`docker compose -f docker-compose.catalog-v3.yml up -d --build` on the
   Garage host) so the catalog becomes schema 2. The 2.0 importer accepts
   catalog schema 1 and 2, so order does not matter.

## Stage 5: docs, versions, release
Docs to update (grep for black/white, /v1, X-Regret, question/response):
- README (install extras: `pipx install 'bad-decisions[aws-deploy]'` for AWS
  management, `[tui]` for the dashboard; API examples on /v2; headers
  X-Client-ID/X-Session-ID/X-Feedback-Token; /v1 is 410).
- docs/CARDDECK.md: pack schema 2 (prompts/answers, prompt `text`), schema 1
  still read; catalog schema 2 (prompt_count/answer_count); LICENSE/ATTRIBUTION
  must match metadata; control characters rejected.
- docs/PEER_PRESSURE.md: v2 protocol (bearer token alone identifies the player;
  join with a token rejoins; POST .../end; heartbeat returns {revision, resync};
  error envelope; caps and 429/503/409 codes).
- docs/DEPLOYMENT.md, EASY_DEPLOY.md, the AWS doc: extras, 410, new settings
  (BAD_DECISIONS_CORS_ORIGINS, PEER_PRESSURE_MAX_ROOMS/MAX_PLAYERS/MIN_FREE_MB,
  RATE_LIMIT_PER_MINUTE), removal of plain `setup`, `--service`.
- docs/ATTRIBUTION.md, client README (together output wording), man pages
  (man/bad-decisions.1, client/man/regret.1: prompt/answer wording, v2,
  `regret doctor` already documented; .TH to 2.0.0 and date; tests enforce).
- New docs/MIGRATING-2.0.md: CLI flags (--prompt-packs/--answer-packs), pack
  schema, API /v1 to /v2 mapping, headers, Peer Pressure changes, extras,
  client Python 3.10+, activator re-bootstrap, catalog schema 2.
- CHANGELOG.md and client/CHANGELOG.md: move [Unreleased] into
  `## [2.0.0] - Bad Decisions 2.0: Terrible choices at terrifying speeds`
  (check_release_tag.py requires the `## [2.0.0]` prefix). Mention the fixed
  1.x bug: `regret feedback` doubled the public prefix (404 behind nginx).
Version bump: four files + `?v=` busters in web/index.html + man .TH
(tests enforce). Then: full validation, build, twine check,
scripts/check_installed.py in a clean venv; delete HANDOFF.md; merge v2 to
main; CI green; tag v2.0.0 (asks the owner first: tags publish to PyPI).

## Operator steps (owner, in order)
1. Before deploying 2.0: rerun the activator bootstrap (it reads schema 2 and
   /v2/packs): `sudo DEPLOY_USER=michael PORT=8001 PACK_DIR=/opt/bad-decisions/packs ./deploy.sh bootstrap-rootless`.
2. Approve the PyPI release; then `pipx install --force 'bad-decisions==2.0.0'`
   and `bad-decisions deploy local`; verify with
   `bad-decisions doctor --url https://bytes.coffee/bad-decisions`.
   Note: the pipx management install stays lean; add `[tui]` if wanted.
3. Upload rebuilt xkcdb; redeploy the catalog indexer (stage 4b.5).
4. Homebrew: `scripts/update_homebrew_formula.py 2.0.0`, drop the formula's
   `packaging` resource (client is dependency-free), add
   `assert_match "installed with homebrew", shell_output("#{bin}/regret doctor --api-url http://127.0.0.1:9 2>&1", 1)`
   to its test, test/audit with brew, commit here and push the tap.
5. Upgrade regret on the Mac (`brew install bytesandcoffee/tap/regret`, then
   `pip uninstall bad-decisions-client` from pyenv).
6. The droplet postflight script's Peer Pressure smoke now speaks v2: it fails
   against 1.x and passes after 2.0 is deployed.
