# Contributing to Bad Decisions

Small, focused improvements are welcome. Open an issue before a broad feature or
architecture change so nobody independently productionizes the same joke twice.

## Development setup

Bad Decisions requires Python 3.12; the dependency-free Regret client supports
Python 3.10+. Work in the repository virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m pytest
PYTHONPATH=client/src .venv/bin/python -m pytest client/tests
bash -n deploy.sh
bash -n deploy/rollback.sh
```

Build checks:

```bash
.venv/bin/python -m build .
.venv/bin/python -m build client
```

See `AGENTS.md` for invariants around archive validation, atomic imports,
immutable runtime packs, deployment boundaries, release metadata, and tests.

## Scope

- Add a regression test with every bug fix.
- Keep one-shot CLI output clean and machine-friendly.
- Preserve stable JSON errors and strict validation.
- Do not add runtime pack-upload or mutation endpoints.
- Avoid unrelated refactors in focused changes.
- Update relevant docs, manual pages, and `patchnotes.md`.

## Card packs

Pack submissions must include source/provenance, creator attribution, a clear
license identifier and notice, and evidence that redistribution is allowed.
Never submit private message corpora, personal data, or third-party content
whose redistribution terms are unknown. Start with
[`examples/minimal-pack`](examples/minimal-pack/) and read
[`docs/CARDDECK.md`](docs/CARDDECK.md).

## Bugs and ideas

Use the issue templates. Include the version, installation method, platform,
exact command, expected behavior, and sanitized output. Never post bearer
tokens, environment files, room capabilities, or service credentials.

Pull requests should explain the user-visible result, identify security or
licensing implications, and list the checks run.
