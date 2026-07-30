# CI Integration

The checked-in GitHub Actions workflow is intentionally aligned with the local
pre-release gate. CI installs the project with development dependencies, checks
the schema mirror explicitly, then runs the same release validation command used
by maintainers.

```bash
python -m pip install -e ".[dev]"
chordchart schemas --check
chordchart release-check
chordchart validate examples/open-string-progression.yaml
chordchart render examples/open-string-progression.yaml --format json | python -m json.tool >/tmp/chordatlas-example.json
```

## GitHub Actions Policy

The workflow preserves the repository action-version policy:

- Third-party actions are pinned to full commit SHAs.
- Version comments document the reviewed upstream version beside each SHA.
- The workflow uses `permissions: contents: read`.

Current workflow steps:

1. Check out the repository.
2. Set up Python 3.11 with pip caching.
3. Install the project with `.[dev]`.
4. Run `chordchart schemas --check`.
5. Run `chordchart release-check`.
6. Verify the installed CLI by validating and rendering the example chart.
7. Re-run snapshot verification with machine-readable diagnostics and diff
   artifacts.
8. Upload any snapshot diff artifacts.

`release-check` is the single authoritative CI gate. It runs schema mirror
validation, snapshot verification, Python compilation, full `pytest`, and
temporary wheel/sdist build inspection.

The workflow repeats snapshot checking after the release gate so CI can emit
clear annotations and upload full diff artifacts. The explicit status condition
lets snapshot diagnostics run after an earlier release-check failure, provided
installation succeeded, while still skipping diagnostics when installation
fails or the workflow is cancelled.

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
chordchart snapshots check research-comparison --format json
chordchart snapshots check research-comparison --diff-dir snapshot-diffs
chordchart snapshots regenerate research-comparison
```

Available snapshot targets are `open-string` and `research-comparison`.
Omitting the target checks or regenerates all snapshot families.

## Local Parity

Before opening or updating a pull request, run:

```bash
chordchart schemas --check
chordchart release-check
chordchart validate examples/open-string-progression.yaml
chordchart render examples/open-string-progression.yaml --format json | python -m json.tool >/tmp/chordatlas-example.json
```

CI should fail for the same reasons as the local release gate. If CI fails,
reproduce locally with the commands above before changing the workflow.

## Snapshot Diagnostics

Snapshot checks remain available for targeted local debugging:

```bash
chordchart snapshots check --format json --diff-dir snapshot-diffs
```

The release gate already checks all registered snapshot families. Use targeted
snapshot commands when you need focused diffs, not as a separate CI substitute.
