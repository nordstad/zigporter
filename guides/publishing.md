# Publishing to PyPI

The project uses GitHub Actions for automated PyPI publishing. **IMPORTANT:** Follow this exact process to avoid workflow failures.

## Quick Reference (TL;DR)

```bash
# 1. Update version and CHANGELOG
vim pyproject.toml   # Change version to "0.2.0"
vim CHANGELOG.md     # Move items from [Unreleased] → [0.2.0] with date

# 2. Commit and push
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.2.0"
git push

# 3. Create and push tag (ONLY!)
git tag v0.2.0
git push origin v0.2.0

# 4. Wait for workflow - it handles everything automatically!
# ✅ Builds and validates package
# ✅ Publishes to PyPI
# ✅ Creates GitHub release (DO NOT create manually!)
# ✅ Updates CHANGELOG.md with release notes

# ❌ DO NOT: gh release create v0.2.0
# ❌ DO NOT: Create release via GitHub UI
```

## ⚠️ CRITICAL: DO NOT Manually Create GitHub Releases

**❌ NEVER DO THIS:**

```bash
# DO NOT create releases manually with gh CLI
gh release create v0.2.0 --title "..." --notes "..."

# DO NOT create releases through GitHub web UI
```

**Why this fails:**

1. Creating a GitHub release manually triggers the publish workflow
2. The workflow tries to create the same release → **FAILS** with:
   ```
   RequestError [HttpError]: Validation Failed:
   {"resource":"Release","code":"already_exists","field":"tag_name"}
   ```
3. Package gets published to PyPI ✅ but workflow shows as failed ❌

**✅ CORRECT APPROACH:** Just push the git tag and let the workflow handle everything.

---

## Correct Publishing Process

### Step 1: Pre-Publish Validation

```bash
# Run all tests
uv run pytest

# Check lint and formatting
uv run ruff check .
uv run ruff format --check .

# Build and smoke test locally
uv build
python -m venv /tmp/smoke-venv
/tmp/smoke-venv/bin/pip install dist/*.whl
/tmp/smoke-venv/bin/zigporter --version
/tmp/smoke-venv/bin/zigporter --help
```

### Step 2: Bump Version and Update CHANGELOG

```bash
# 1. Edit pyproject.toml — change version = "0.2.0"

# 2. Edit CHANGELOG.md — move [Unreleased] items to new version:
#
#   ## [Unreleased]
#
#   ## [0.2.0] - 2026-03-01
#   ### Added
#   - New feature X
```

### Step 3: Commit Version Bump

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.2.0"
git push origin main
```

### Step 4: Create and Push Git Tag ONLY

```bash
git tag v0.2.0
git push origin v0.2.0

# ❌ DO NOT run: gh release create v0.2.0
# The workflow creates the release automatically!
```

### Step 5: Let the Workflow Handle Everything

The workflow (`.github/workflows/publish.yml`) automatically:

1. ✅ Builds the package and validates metadata with twine
2. ✅ Runs smoke tests against the built wheel
3. ✅ Publishes to PyPI with trusted publishing (no API tokens needed)
4. ✅ Creates a GitHub Release with auto-generated release notes
5. ✅ Updates `CHANGELOG.md` and commits it back to main

## Monitoring the Release

### Verify Homebrew tap (after CI completes)

```bash
# Check tap formula was updated to new version
gh api repos/nordstad/homebrew-zigporter/contents/Formula/zigporter.rb \
  | python3 -c "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())" \
  | head -8

# Smoke-test the install
brew upgrade nordstad/zigporter/zigporter && zigporter --version
```

If `brew upgrade` fails, check if resource stanzas need updating:
`brew update-python-resources nordstad/zigporter/zigporter` then push to tap main.

```bash
# View recent workflow runs
gh run list --workflow=publish.yml --limit 5

# Watch the current run
gh run watch

# View logs if there are failures
gh run view --log-failed
```

## Troubleshooting

### Issue: "Release already exists"

**Symptoms:**
```
RequestError [HttpError]: Validation Failed:
{"resource":"Release","code":"already_exists","field":"tag_name"}
```

**Cause:** You manually created a GitHub release.

**Fix:**
```bash
# 1. Delete the manual release
gh release delete v0.2.0 --yes

# 2. Re-run the failed workflow job
gh run list --workflow=publish.yml --limit 1
gh run rerun <run-id> --failed
```

### Issue: Need to redo a release

PyPI does **not** allow republishing the same version. If the build stage failed before publishing, you can delete the tag and retry. If it already published to PyPI, you must increment to a patch version.

```bash
# Delete tag locally and remotely
git tag -d v0.2.0
git push origin --delete v0.2.0

# Fix the issue, then release as v0.2.1
git tag v0.2.1
git push origin v0.2.1
```

## Version Numbering

Follow semantic versioning:

- **Major (1.0.0)**: Breaking changes, incompatible CLI or API changes
- **Minor (0.2.0)**: New features, backward-compatible
- **Patch (0.1.1)**: Bug fixes, backward-compatible

For pre-releases, append a suffix:

```toml
version = "0.2.0-beta.1"
```

The workflow automatically marks releases with `-` in the version as pre-releases on GitHub.
