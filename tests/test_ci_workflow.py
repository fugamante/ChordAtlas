import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_runs_snapshot_json_check_with_annotations_and_artifacts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "chordchart snapshots check --format json --diff-dir snapshot-diffs" in workflow
    assert "> snapshot-result.json" in workflow
    assert 'for filename in payload["drift_files"]' in workflow
    assert "::error file={filename}::Snapshot drift detected" in workflow
    assert "path: snapshot-diffs" in workflow
    assert "if-no-files-found: ignore" in workflow

    upload_pin = re.search(r"actions/upload-artifact@([0-9a-f]{40})", workflow)
    assert upload_pin is not None
    assert "<PINNED_UPLOAD_ARTIFACT_SHA>" not in workflow
