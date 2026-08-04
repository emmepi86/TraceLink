# Releasing

The PyPI package is **`tracelink-vault`** (the bare name `tracelink` on PyPI
belongs to an unrelated project). The installed command is `tracelink`.

## One-time setup: PyPI trusted publishing

Publishing runs from GitHub Actions with no long-lived token, via
[trusted publishing](https://docs.pypi.org/trusted-publishers/). Before the
first release, register the publisher on pypi.org:

1. Log in to pypi.org → **Your account → Publishing**.
2. Under **Add a new pending publisher**, fill in:
   - PyPI project name: `tracelink-vault`
   - Owner: `emmepi86`
   - Repository: `TraceLink`
   - Workflow name: `publish.yml`
   - Environment: `pypi`
3. Save. The first successful run of the workflow claims the name and creates
   the project.

## Cutting a release

1. Bump the version everywhere and verify lockstep:

   ```bash
   python3 scripts/bump.py --set X.Y.Z
   python3 scripts/bump.py --check
   ```

2. Update `CHANGELOG.md`, commit, and push to `main`.
3. Tag and push the tag — the tag is what triggers the publish workflow:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. The `publish` workflow builds the sdist+wheel and uploads to PyPI. Tests run
   first; a red test suite blocks the upload.

`scripts/bump.py --check` also runs in CI on every push, so a version drift
between `pyproject.toml`, `src/tracelink/__init__.py`,
`.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` fails the
build rather than shipping.
