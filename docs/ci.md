# CI Integration

This page documents the repository's snapshot drift reporting workflow and the
portable pattern used by `.github/workflows/ci.yml`.

## Snapshot JSON

Use JSON output when CI needs structured snapshot status:

```bash
chordchart snapshots check --format json --diff-dir snapshot-diffs > snapshot-result.json
```

Exit codes are unchanged:

- `0`: snapshots are in sync.
- `1`: snapshot drift was detected.
- `2`: the command was invalid, such as an unknown target.

The JSON payload includes:

- `status`: `clean` or `dirty`
- `target`: requested snapshot target
- `checked_files`: all checked snapshot files
- `drift_files`: snapshot files with drift
- `diffs`: per-file diff metadata
- `diffs[].truncated`: whether inline text-mode diff output would be capped
- `diffs[].diff_artifact`: full `.diff` path when `--diff-dir` is used

## GitHub Actions

This example keeps snapshot checking separate from `pytest` so CI can emit clear
annotations and upload full diff artifacts. The explicit status condition lets
snapshot generation run after an earlier test failure, provided installation
succeeded, while still skipping diagnostics when installation fails or the
workflow is cancelled.

```yaml
- name: Install project
  id: install
  run: python -m pip install -e ".[dev]"

- name: Check snapshots
  id: snapshots
  if: ${{ !cancelled() && steps.install.outcome == 'success' }}
  shell: bash
  run: |
    set +e
    chordchart snapshots check --format json --diff-dir snapshot-diffs > snapshot-result.json
    status=$?
    set -e

    python - <<'PY'
    import json
    from pathlib import Path

    payload = json.loads(Path("snapshot-result.json").read_text())
    for filename in payload["drift_files"]:
        print(f"::error file={filename}::Snapshot drift detected")

    for item in payload["diffs"]:
        artifact = item.get("diff_artifact")
        if artifact:
            print(f"Diff artifact for {item['file']}: {artifact}")
    PY

    exit "$status"
```

The explicit condition replaces GitHub Actions' implicit `success()` condition
only after the required installation has succeeded. This lets snapshot
diagnostics survive an ordinary `pytest` failure without attempting to invoke a
missing CLI after installation failure, and it preserves cancellation. It does
not mask snapshot drift: the command's original exit status still fails the job
after annotations and diff files are produced. The workflow does not use
`continue-on-error` for snapshot validation.

Use the same prerequisite and cancellation condition for artifact upload. This
keeps upload available after test or snapshot failure, but prevents it from
running after installation failure or cancellation. Follow the repository's
action pinning policy; use an audited full commit SHA rather than a floating tag.

```yaml
- name: Upload snapshot diffs
  if: ${{ !cancelled() && steps.install.outcome == 'success' }}
  uses: actions/upload-artifact@<PINNED_UPLOAD_ARTIFACT_SHA>
  with:
    name: snapshot-diffs
    path: snapshot-diffs
    if-no-files-found: ignore
```

Replace `<PINNED_UPLOAD_ARTIFACT_SHA>` with the reviewed commit SHA selected for
your repository. Do not use this placeholder in a committed workflow.

The checked-in CI workflow currently pins `actions/upload-artifact` to the
reviewed Node 24-native `v7.0.1` commit SHA and uploads `snapshot-diffs` only
when diff files exist.

## Trusted Publishing

`.github/workflows/publish.yml` is the canonical PyPI Trusted Publisher
workflow. It runs only when a non-prerelease GitHub release is published; push,
pull-request, and manual-dispatch events cannot invoke it. The build job checks
out the release tag and requires all of the following before building:

- the tag is exactly `v` followed by the version in `pyproject.toml`;
- the tag is annotated;
- the checked-out commit is the tag target; and
- the tag target is reachable from `origin/main`.

The build job has only `contents: read`. It builds and checks the wheel and
source distribution, then transfers them through a one-day GitHub Actions
artifact. A separate `publish` job uses the protected `pypi` environment and is
the only job granted `id-token: write`. It does not receive a username, password,
or repository token for PyPI. All referenced actions are pinned to reviewed full
commit SHAs, and an existing version is never skipped or overwritten.

Configure the existing PyPI project with this exact trusted-publisher identity:

- Owner: `fugamante`
- Repository: `ChordAtlas`
- Workflow filename: `publish.yml`
- Environment: `pypi`

The GitHub `pypi` environment must require approval and allow only version tags
matching `v*`. Keep the project-scoped API token as a fallback until a separately
authorized future release proves the OIDC exchange and upload end to end. Do not
rerun the already-published `v0.1.0` release to test this workflow.

## Targeted Checks

CI can check only one snapshot family when a job is scoped:

```bash
chordchart snapshots check open-string --format json
chordchart snapshots check research-comparison --format json --diff-dir snapshot-diffs
```

Use `chordchart snapshots list` to enumerate supported targets.
