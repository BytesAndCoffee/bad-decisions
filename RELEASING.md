# Releasing

Both packages (`bad-decisions` server and `bad-decisions-client`) are released
together, with the same version, by pushing a tag. GitHub Actions builds,
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
2. Add the change to `patchnotes.md`, run the checks in `AGENTS.md`, commit, and
   push `main`. Wait for the **CI** workflow to pass.
3. Tag the commit and push the tag (this is what publishes):

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. Approve the `pypi` environment when the **Release** workflow pauses.
5. Confirm the new version on PyPI and deploy the server if needed.

## What the workflow checks

- The tag is `vX.Y.Z`, is reachable from `main`, and matches every package
  version (`scripts/check_release_tag.py`).
- Server tests, client tests, and `bash -n` on the deploy scripts pass.
- Both packages build once and pass `twine check`; the same built files are
  published, never rebuilt.

## Notes

- PyPI versions are immutable. Never retag different contents under a released
  version; bump to the next patch instead.
- If the publish job fails after only one package uploaded, re-run it: existing
  files are skipped and the rest are uploaded.
- Do not upload locally built files once this workflow exists; CI builds are the
  canonical artifacts. A local `twine upload` is only a break-glass fallback.
- Pushing a `v*` tag publishes to PyPI. Treat it like `twine upload`.
