# 2.0.0 handoff (branch `v2`; delete this file before merging to main)

Read AGENTS.md first. The plan and its checkboxes are in patchnotes.md under
"2.0.0 plan"; tick items there as they land. Each stage is one commit on `v2`.

## Decisions (owner, 2026-09-27)
- Unify on prompt/answer(s). Card IDs already in packs stay as they are.
- `/v1` API is removed entirely (owner is the only consumer); `/v2` replaces it.
  `/v1/*` returns 410 with an upgrade hint; `/healthz` stays unversioned.
- Schema-1 packs stay readable; everything written is schema 2.
- Server-sent events are deferred to 2.1; design the v2 room API to allow them.
- Everything ships as 2.0.0; there is no 1.8.6.

## Status
- [x] Stage 1: prompt/answer rename and pack schema 2 (commit a4b1fd1).
- [x] Stage 2: `[aws]` (boto3), `[aws-deploy]` (+CDK), `[tui]` extras; requirements.lock is server-only, requirements-extras.lock pins the rest.
- [x] Stage 3: `/v2` API (src/bad_decisions/api.py rewritten), regret/web/doctor/smoke/activator clients moved to v2; tests/test_api_v2.py pins the contract.
- [ ] Stage 4: dead code and backlog.
- [ ] Stage 5: docs, man pages, migration guide, 2.0.0 release, deploy, Homebrew.

## Release notes for the operator
- The activator changed (reads schema 2): the owner must rerun
  `sudo ... ./deploy.sh bootstrap-rootless` (see AGENTS.local.md) before
  deploying 2.0 or using `pack replace-local` with 2.0.
- The object-archive catalog indexer changed (catalog schema 2): redeploy it
  with deploy/object-archive/docker-compose.catalog-v3.yml after the server.

## Docs to update in stage 5
- README/DEPLOYMENT/EASY_DEPLOY/AWS docs: install extras (`pipx install 'bad-decisions[aws-deploy]'` for AWS management, `[tui]` for the dashboard).
- CARDDECK.md: pack schema 2 (prompts/answers/text), schema 1 still read; catalog schema 2.- PEER_PRESSURE.md: v2 protocol (token-only identity, POST end, heartbeat {revision, resync}, error envelope, caps, rate limits).
- API reference in README: /v2 paths, headers X-Client-ID/X-Session-ID/X-Feedback-Token, 410 for /v1.
- Operator: `scripts/smoke_peer_pressure.py` now speaks v2, so the owner's droplet postflight will fail against 1.x until 2.0 is deployed.
- Fixed 1.x bug worth a changelog line: `regret feedback` doubled the public prefix in the feedback URL (404 behind nginx).
