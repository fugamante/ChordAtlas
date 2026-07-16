import json
import shutil
from pathlib import Path

from chordatlas.cli import main
from chordatlas.schema import SchemaValidationResult

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_EXAMPLE = "examples/research-recording-comparison.yaml"


def _comparison_chart_yaml() -> str:
    return "\n".join(
        [
            "title: Test",
            "recordings:",
            "  studio: Studio recording",
            "  live: Live performance",
            "structured_recording_notes:",
            "  Effects:",
            "    category: effects",
            "    notes:",
            "      - text: Light chorus",
            "        recording_id: studio",
            "        severity: medium",
            "      - text: Dryer amp tone",
            "        recording_id: live",
            "",
        ]
    )


def _provenance_comparison_chart_yaml() -> str:
    return "\n".join(
        [
            "title: Test",
            "recordings:",
            "  studio:",
            "    title: Studio recording",
            "    source_url: https://example.invalid/studio",
            "    version_label: Studio reference",
            "structured_recording_notes:",
            "  Estimated tuning:",
            "    category: tuning",
            "    notes:",
            "      - text: Approximately 15 cents flat",
            "        recording_id: studio",
            "        confidence: medium",
            "        claim_origin: inferred",
            "        provenance:",
            "          source_type: audio",
            "          source_name: Studio recording",
            "          timestamp_range: 00:00-00:10",
            "          method: tuner comparison",
            "          confidence: medium",
            "          verification_status: verified",
            "          claim_origin: inferred",
            "          evidence_refs:",
            "            - ref_id: studio-00-00",
            "",
        ]
    )


def _copy_snapshot_workspace(tmp_path: Path) -> Path:
    (tmp_path / "examples").mkdir()
    (tmp_path / "tests" / "snapshots").mkdir(parents=True)
    for name in ("open-string-progression.yaml", "research-recording-comparison.yaml"):
        shutil.copyfile(ROOT / "examples" / name, tmp_path / "examples" / name)
    for path in (ROOT / "tests" / "snapshots").iterdir():
        if path.is_file():
            shutil.copyfile(path, tmp_path / "tests" / "snapshots" / path.name)
    return tmp_path


def test_render_outputs_markdown(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "md"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("# Test\n")
    assert "## Chord Reference" in captured.out
    assert "### G" in captured.out
    assert captured.err == ""


def test_render_outputs_json_contract(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "json"]) == 0

    captured = capsys.readouterr()
    assert '"schema_version": "1.0.0"' in captured.out
    assert '"title": "Test"' in captured.out
    assert captured.err == ""


def test_render_reports_malformed_yaml(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["render", str(chart_path), "--format", "md"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid chart: Expected YAML mapping" in captured.err


def test_compare_outputs_json_contract(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json"]) == 0

    captured = capsys.readouterr()
    assert '"comparison_schema_version": "1.0.0"' in captured.out
    assert '"source_specific_claim_count": 2' in captured.out
    assert captured.err == ""


def test_compare_outputs_markdown(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "md"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("# Recording Comparison: Test\n")
    assert "## Summary" in captured.out
    assert "## Effects" in captured.out
    assert captured.err == ""


def test_compare_outputs_text(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "txt"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("Recording Comparison: Test\n")
    assert "Summary" in captured.out
    assert "EFFECTS" in captured.out
    assert captured.err == ""


def test_compare_outputs_filtered_csv(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--category",
                "effects",
                "--recording",
                "studio",
                "--severity",
                "medium",
                "--source-specific-only",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific"
    )
    assert (
        "effects,Effects,studio,Studio recording,Effects,Light chorus,medium,true"
        in captured.out
    )
    assert "Dryer amp tone" not in captured.out
    assert captured.err == ""


def test_compare_outputs_provenance_aware_json(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json", "--provenance", "research"]) == 0

    captured = capsys.readouterr()
    assert '"claim_origin": "inferred"' in captured.out
    assert '"evidence_refs": [' in captured.out
    assert '"source_url": "https://example.invalid/studio"' in captured.out
    assert captured.err == ""


def test_compare_outputs_extended_csv_when_provenance_is_requested(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "csv", "--provenance", "standard"]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific,confidence,claim_origin,provenance_summary,"
        "evidence_refs,recording_source_url,recording_version_label"
    )
    assert "medium,inferred," in captured.out
    assert "https://example.invalid/studio,Studio reference" in captured.out
    assert captured.err == ""


def test_compare_writes_metadata_json_sidecar_for_research_csv(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "comparison.metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("category,category_label,recording_id")
    assert captured.err == ""
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"metadata_schema_version": "1.0.0"' in metadata
    assert '"ref_id": "studio-00-00"' in metadata
    assert '"source_url": "https://example.invalid/studio"' in metadata


def test_compare_research_example_outputs_filtered_csv_and_metadata_sidecar(
    tmp_path,
    capsys,
) -> None:
    metadata_path = tmp_path / "research.metadata.json"

    assert (
        main(
            [
                "compare",
                RESEARCH_EXAMPLE,
                "--format",
                "csv",
                "--provenance",
                "research",
                "--category",
                "effects",
                "--recording",
                "remaster",
                "--severity",
                "medium",
                "--source-specific-only",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific,confidence,claim_origin,provenance_summary,"
        "evidence_refs,recording_source_url,recording_version_label"
    )
    assert "Brighter high-end EQ" in captured.out
    assert "https://example.invalid/audio/remaster,2018 remaster" in captured.out
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"metadata_schema_version": "1.0.0"' in metadata
    assert '"categories": [' in metadata
    assert '"effects"' in metadata
    assert '"ref_id": "remaster-booklet"' in metadata
    assert captured.err == ""


def test_compare_rejects_metadata_json_for_non_csv_output(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "json",
                "--provenance",
                "research",
                "--metadata-json",
                str(tmp_path / "metadata.json"),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--metadata-json is only supported with --format csv" in captured.err


def test_compare_rejects_metadata_json_without_research_provenance(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--metadata-json",
                str(tmp_path / "metadata.json"),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--metadata-json requires --provenance research" in captured.err


def test_compare_reports_unwritable_metadata_json_path(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(tmp_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to write metadata JSON:" in captured.err


def test_compare_reports_invalid_recording_filter(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--recording", "missing"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid comparison filter: Unknown recording filter(s): missing" in captured.err


def test_compare_reports_malformed_yaml(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid chart: Expected YAML mapping" in captured.err


def test_validate_accepts_valid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\nsections: []\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped" not in captured.err
    assert captured.out == ""


def test_validate_reports_provenance_warnings(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "provenance:",
                "  source_type: unknown",
                "  claim_origin: unknown",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart with 1 provenance warning(s):" in captured.err
    assert "- chart has unknown provenance" in captured.err
    assert captured.out == ""


def test_validate_reports_schema_failure_and_provenance_warnings(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "provenance:",
                "  source_type: unknown",
                "  claim_origin: unknown",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "chordatlas.cli.validate_chart_schema",
        lambda chart: SchemaValidationResult(status="failed", errors=("sections: missing",)),
    )

    assert main(["validate", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert "Invalid chart: normalized JSON Schema validation failed" in captured.err
    assert "- sections: missing" in captured.err
    assert "Provenance warning(s): 1" in captured.err
    assert "- chart has unknown provenance" in captured.err
    assert captured.out == ""


def test_validate_reports_schema_skip(tmp_path, capsys, monkeypatch) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\nsections: []\n", encoding="utf-8")

    monkeypatch.setattr(
        "chordatlas.cli.validate_chart_schema",
        lambda chart: SchemaValidationResult(
            status="skipped",
            reason="jsonschema is not installed",
        ),
    )

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped: jsonschema is not installed" in captured.err
    assert captured.out == ""


def test_validate_rejects_invalid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert "Invalid chart: Expected YAML mapping" in captured.err
    assert captured.out == ""


def test_schemas_check_accepts_clean_mirror(capsys) -> None:
    assert main(["schemas", "--check"]) == 0

    captured = capsys.readouterr()
    assert "Schema mirror is in sync:" in captured.err
    assert captured.out == ""


def test_schemas_check_reports_drift(tmp_path, capsys, monkeypatch) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    schema_dir.joinpath("song-chart.schema.json").write_text("{}", encoding="utf-8")
    schema_dir.joinpath("provenance-record.schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--check"]) == 1

    captured = capsys.readouterr()
    assert "Schema mirror drift detected:" in captured.err
    assert "- song-chart.schema.json" in captured.err
    assert "chordchart schemas --sync" in captured.err
    assert captured.out == ""


def test_schemas_sync_updates_drifted_mirror(tmp_path, capsys, monkeypatch) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    schema_dir.joinpath("song-chart.schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--sync"]) == 0
    assert main(["schemas", "--check"]) == 0

    captured = capsys.readouterr()
    assert "Synced schema mirror:" in captured.err
    assert "Schema mirror is in sync:" in captured.err
    assert captured.out == ""


def test_snapshots_list_outputs_targets(capsys) -> None:
    assert main(["snapshots", "list"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "open-string\nresearch-comparison\n"
    assert captured.err == ""


def test_snapshots_check_accepts_clean_target(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshots are in sync: research-comparison" in captured.err


def test_snapshots_check_outputs_clean_json(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison", "--format", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "clean"
    assert payload["target"] == "research-comparison"
    assert payload["drift_files"] == []
    assert payload["diffs"] == []
    assert "research-recording-comparison.effects-remaster.csv" in payload["checked_files"]
    assert captured.err == ""


def test_snapshots_check_reports_drift(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot drift detected: research-comparison" in captured.err
    assert "- research-recording-comparison.effects-remaster.csv" in captured.err
    assert "--- snapshot/research-recording-comparison.effects-remaster.csv" in captured.err
    assert "+++ generated/research-recording-comparison.effects-remaster.csv" in captured.err
    assert "-drift" in captured.err


def test_snapshots_check_outputs_drift_json(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison", "--format", "json"]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "dirty"
    assert payload["target"] == "research-comparison"
    assert payload["drift_files"] == ["research-recording-comparison.effects-remaster.csv"]
    assert payload["diffs"][0]["file"] == "research-recording-comparison.effects-remaster.csv"
    assert payload["diffs"][0]["truncated"] is False
    assert payload["diffs"][0]["diff_artifact"] is None
    assert captured.err == ""


def test_snapshots_check_writes_diff_artifacts(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    diff_dir = workspace / "snapshot-diffs"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    diff_path = diff_dir / "research-recording-comparison.effects-remaster.csv.diff"
    assert diff_path.exists()
    assert "--- snapshot/research-recording-comparison.effects-remaster.csv" in diff_path.read_text(
        encoding="utf-8"
    )
    assert f"Full diffs written to: {diff_dir}" in captured.err


def test_snapshots_check_json_reports_diff_artifact_paths(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    diff_dir = workspace / "snapshot-diffs"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--format",
                "json",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact = Path(payload["diffs"][0]["diff_artifact"])
    assert artifact.exists()
    assert artifact == (diff_dir / "research-recording-comparison.effects-remaster.csv.diff")
    assert captured.err == ""


def test_snapshots_check_truncates_large_inline_diffs(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "open-string-progression.md"
    snapshot.write_text(
        "\n".join(f"drift line {index}" for index in range(200)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "open-string"]) == 1

    captured = capsys.readouterr()
    assert "... diff truncated," in captured.err


def test_snapshots_regenerate_updates_drifted_target(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "regenerate", "research-comparison"]) == 0
    assert main(["snapshots", "check", "research-comparison"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Regenerated snapshots: research-comparison" in captured.err
    assert "Snapshots are in sync: research-comparison" in captured.err


def test_snapshots_rejects_unknown_target(capsys) -> None:
    assert main(["snapshots", "check", "missing"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unknown snapshot target: missing" in captured.err


def test_release_check_command_delegates(capsys, monkeypatch) -> None:
    monkeypatch.setattr("chordatlas.cli.run_release_check", lambda: 0)

    assert main(["release-check"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""


def test_release_check_command_reports_delegate_failure(capsys, monkeypatch) -> None:
    monkeypatch.setattr("chordatlas.cli.run_release_check", lambda: 1)

    assert main(["release-check"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
