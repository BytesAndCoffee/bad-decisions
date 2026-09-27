# Releasing

Both packages (`bad-decisions` server and `bad-decisions-client`) are released
together, with the same version, by pushing a tag. Every release is lockstep: a
server release always publishes a matching client version, and vice versa. GitHub Actions builds,
validates, and publishes them to PyPI using Trusted Publishing (OIDC), so no
PyPI token is stored anywhere. Deploying the server to a host is a separate,
manual step (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## One-time setup (repository owner)

1. **PyPI trusted publishers.** For each project (`bad-decisions` and
   `bad-decisions-client`), open *Manage > Publishing* on pypi.org and add a
   GitHub publisher: owner `BytesAndCoffee`, repository `bad-decisions`,
   workflow `release.yml`, environment `pypi`.
2. **GitHub environment.** In the repository, create an environment named
   `pypi` and add yourself as a required reviewer, so a tag alone cannot publish.
3. **Tag protection (optional).** Restrict who can create `v*` tags.

Workflows run in the repository the package metadata links to
(`BytesAndCoffee/bad-decisions`), which is the `origin` remote.

## Release procedure

1. Bump the version in all four sources and the `?v=` cache busters in
   `src/bad_decisions/web/index.html`. `tests/test_release_versions.py` fails if
   any of them disagree.
2. Update the manual pages, `man/bad-decisions.1` and `client/man/regret.1`:
   the `.TH` date and version, plus every command, option, environment
   variable, and file this release adds or changes. Preview each page with
   `man -l <page>`. `tests/test_manpages.py` catches a stale version or an
   undocumented command or setting.
3. Add the change to `patchnotes.md`, run the checks in `AGENTS.md`, commit, and
   push `main`. Wait for the **CI** workflow to pass.
4. Tag the commit and push the tag (this is what publishes):

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

5. Approve the `pypi` environment when the **Release** workflow pauses.
6. Confirm the new version on PyPI and deploy the server if needed.
7. Update the Homebrew formula for the client in the
   [BytesAndCoffee/homebrew-tap](https://github.com/BytesAndCoffee/homebrew-tap)
   tap:

   ```bash
   .venv/bin/python scripts/update_homebrew_formula.py X.Y.Z
   cp homebrew/regret.rb "$(brew --repository bytesandcoffee/tap)/Formula/regret.rb"
   brew install --build-from-source bytesandcoffee/tap/regret   # or brew upgrade
   brew test bytesandcoffee/tap/regret
   brew audit --strict --formula bytesandcoffee/tap/regret
   ```

   Commit `homebrew/regret.rb` here, then commit and push the same file in the
   tap. `brew update-python-resources` refreshes the pinned resources when the
   client's dependencies change.

## What the workflow checks

- The tag is `vX.Y.Z`, is reachable from `main`, matches every package
  version, and both changelogs have a `## [X.Y.Z]` section
  (`scripts/check_release_tag.py`).
- Server tests, client tests, and `bash -n` on the deploy scripts pass.
- Both packages build once and pass `twine check`; the same built files are
  published, never rebuilt.
- Those exact wheels are installed into a clean virtualenv with
  `requirements.lock`, and `scripts/check_installed.py` checks the console
  scripts and `--version`, the bundled packs, the packaged lock, the manual
  pages, and a live `bad-decisions serve` (`/healthz`, `/v1/round`, `/web/`).

## Notes

- PyPI versions are immutable. Never retag different contents under a released
  version; bump to the next patch instead.
- If the publish job fails after only one package uploaded, re-run it: existing
  files are skipped and the rest are uploaded.
- Do not upload locally built files once this workflow exists; CI builds are the
  canonical artifacts. A local `twine upload` is only a break-glass fallback.
- Pushing a `v*` tag publishes to PyPI. Treat it like `twine upload`.
