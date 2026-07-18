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
snapshot generation run after an earlier test failure while still skipping the
step when the workflow is cancelled.

```yaml
- name: Check snapshots
  id: snapshots
  if: ${{ !cancelled() }}
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

`!cancelled()` replaces GitHub Actions' implicit `success()` condition only for
this step. It does not mask snapshot drift: the command's original exit status
still fails the job after annotations and diff files are produced. The workflow
does not use `continue-on-error` for snapshot validation.

To upload diff artifacts, add an upload step that always runs after the snapshot
check. Follow the repository's action pinning policy; use an audited full commit
SHA rather than a floating tag.

```yaml
- name: Upload snapshot diffs
  if: always()
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

## Targeted Checks

CI can check only one snapshot family when a job is scoped:

```bash
chordchart snapshots check open-string --format json
chordchart snapshots check research-comparison --format json --diff-dir snapshot-diffs
```

Use `chordchart snapshots list` to enumerate supported targets.
