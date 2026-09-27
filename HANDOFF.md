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
- [ ] Stage 2: `[aws]` and `[tui]` extras.
- [ ] Stage 3: `/v2` API and security fixes (see patchnotes "API v2").
- [ ] Stage 4: dead code and backlog.
- [ ] Stage 5: docs, man pages, migration guide, 2.0.0 release, deploy, Homebrew.

## Release notes for the operator
- The activator changed (reads schema 2): the owner must rerun
  `sudo ... ./deploy.sh bootstrap-rootless` (see AGENTS.local.md) before
  deploying 2.0 or using `pack replace-local` with 2.0.
- The object-archive catalog indexer changed (catalog schema 2): redeploy it
  with deploy/object-archive/docker-compose.catalog-v3.yml after the server.
