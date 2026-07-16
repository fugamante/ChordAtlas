import json
import importlib
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from chordatlas.compare import comparison_metadata_to_mapping, comparison_to_mapping
from chordatlas.io import load_song_chart, song_chart_to_json
from chordatlas.models import SongChart
from chordatlas.provenance import (
    ClaimOrigin,
    Confidence,
    ProvenanceRecord,
    SourceType,
    VerificationStatus,
)
from chordatlas.schema import check_schema_mirror, sync_schema_mirror, validate_chart_schema


SCHEMAS = Path("schemas")
SONG_SCHEMA_PATH = SCHEMAS / "song-chart.schema.json"
PROVENANCE_SCHEMA_PATH = SCHEMAS / "provenance-record.schema.json"
RECORDING_COMPARISON_SCHEMA_PATH = SCHEMAS / "recording-comparison.schema.json"
RECORDING_COMPARISON_METADATA_SCHEMA_PATH = (
    SCHEMAS / "recording-comparison-metadata.schema.json"
)
EXAMPLE_PATH = Path("examples/open-string-progression.yaml")
RESEARCH_EXAMPLE_PATH = Path("examples/research-recording-comparison.yaml")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    provenance_schema = _json(PROVENANCE_SCHEMA_PATH)
    comparison_schema = _json(RECORDING_COMPARISON_SCHEMA_PATH)
    registry = Registry().with_resource(
        provenance_schema["$id"],
        Resource.from_contents(provenance_schema),
    )
    return registry.with_resource(
        comparison_schema["$id"],
        Resource.from_contents(comparison_schema),
    )


def _song_validator() -> Draft202012Validator:
    return Draft202012Validator(_json(SONG_SCHEMA_PATH), registry=_registry())


def _provenance_validator() -> Draft202012Validator:
    return Draft202012Validator(_json(PROVENANCE_SCHEMA_PATH))


def _comparison_validator() -> Draft202012Validator:
    return Draft202012Validator(_json(RECORDING_COMPARISON_SCHEMA_PATH), registry=_registry())


def _comparison_metadata_validator() -> Draft202012Validator:
    return Draft202012Validator(
        _json(RECORDING_COMPARISON_METADATA_SCHEMA_PATH),
        registry=_registry(),
    )


def _export_payload(path: Path) -> dict:
    chart = load_song_chart(path)
    return json.loads(song_chart_to_json(chart))


def test_contract_schemas_are_valid_json_schemas() -> None:
    Draft202012Validator.check_schema(_json(SONG_SCHEMA_PATH))
    Draft202012Validator.check_schema(_json(PROVENANCE_SCHEMA_PATH))
    Draft202012Validator.check_schema(_json(RECORDING_COMPARISON_SCHEMA_PATH))
    Draft202012Validator.check_schema(_json(RECORDING_COMPARISON_METADATA_SCHEMA_PATH))


def test_provenance_record_schema_accepts_model_serialization() -> None:
    record = ProvenanceRecord(
        source_type=SourceType.AUDIO,
        timestamp_range="00:13-00:18",
        method="human-ear transcription",
        confidence=Confidence.MEDIUM,
        verification_status=VerificationStatus.UNVERIFIED,
        claim_origin=ClaimOrigin.INFERRED,
    )

    _provenance_validator().validate(record.to_mapping())


def test_provenance_record_schema_requires_confidence_for_inferred_claims() -> None:
    errors = list(
        _provenance_validator().iter_errors(
            {
                "source_type": "audio",
                "verification_status": "unverified",
                "claim_origin": "inferred",
            }
        )
    )

    assert any("confidence" in error.message for error in errors)


def test_provenance_record_schema_requires_evidence_for_verified_claims() -> None:
    errors = list(
        _provenance_validator().iter_errors(
            {
                "source_type": "reference",
                "verification_status": "verified",
                "claim_origin": "verified",
            }
        )
    )

    assert any("evidence_refs" in error.message for error in errors)


def test_example_yaml_export_matches_song_chart_schema() -> None:
    payload = _export_payload(EXAMPLE_PATH)

    assert payload["schema_version"] == "1.0.0"
    _song_validator().validate(payload)


def test_research_example_yaml_export_matches_song_chart_schema() -> None:
    payload = _export_payload(RESEARCH_EXAMPLE_PATH)

    assert payload["schema_version"] == "1.0.0"
    assert payload["title"] == "Research Recording Comparison Study"
    _song_validator().validate(payload)


def test_example_comparison_export_matches_recording_comparison_schema() -> None:
    chart = load_song_chart(EXAMPLE_PATH)
    payload = comparison_to_mapping(chart)

    assert payload["comparison_schema_version"] == "1.0.0"
    _comparison_validator().validate(payload)


def test_research_comparison_export_matches_recording_comparison_schema() -> None:
    chart = load_song_chart(EXAMPLE_PATH)
    payload = comparison_to_mapping(chart, provenance_mode="research")

    assert payload["comparison_schema_version"] == "1.0.0"
    _comparison_validator().validate(payload)


def test_research_example_comparison_export_matches_recording_comparison_schema() -> None:
    chart = load_song_chart(RESEARCH_EXAMPLE_PATH)
    payload = comparison_to_mapping(chart, provenance_mode="research")

    assert payload["summary"]["recording_count"] == 4
    assert payload["summary"]["source_specific_claim_count"] > 0
    _comparison_validator().validate(payload)


def test_comparison_metadata_export_matches_recording_comparison_metadata_schema() -> None:
    chart = load_song_chart(EXAMPLE_PATH)
    payload = comparison_metadata_to_mapping(chart)

    assert payload["metadata_schema_version"] == "1.0.0"
    _comparison_metadata_validator().validate(payload)


def test_research_example_metadata_export_matches_recording_comparison_metadata_schema() -> None:
    chart = load_song_chart(RESEARCH_EXAMPLE_PATH)
    payload = comparison_metadata_to_mapping(chart)

    assert payload["metadata_schema_version"] == "1.0.0"
    assert len(payload["evidence_refs"]) >= 6
    _comparison_metadata_validator().validate(payload)


def test_simple_yaml_export_matches_song_chart_schema(tmp_path) -> None:
    chart_path = tmp_path / "simple.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Simple Chart",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G, D/F#]",
                "      - [Em7]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = _export_payload(chart_path)

    assert payload["schema_version"] == "1.0.0"
    assert payload["sections"][0]["bars"] == [["G", "D/F#"], ["Em7"]]
    _song_validator().validate(payload)


def test_schema_rejects_unknown_export_fields() -> None:
    payload = _export_payload(EXAMPLE_PATH)
    payload["unexpected"] = True

    errors = list(_song_validator().iter_errors(payload))

    assert any("Additional properties" in error.message for error in errors)


def test_runtime_schema_validator_accepts_normalized_chart() -> None:
    chart = load_song_chart(EXAMPLE_PATH)

    result = validate_chart_schema(chart)

    assert result.passed


def test_runtime_schema_validator_does_not_require_repo_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = validate_chart_schema(SongChart.from_mapping({"title": "Test"}))

    assert result.passed


def test_runtime_schema_validator_skips_when_jsonschema_is_missing(monkeypatch) -> None:
    real_import = importlib.import_module

    def fake_import(name: str):
        if name == "jsonschema":
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr("chordatlas.schema.importlib.import_module", fake_import)

    result = validate_chart_schema(SongChart.from_mapping({"title": "Test"}))

    assert result.skipped
    assert result.reason == "jsonschema is not installed"


def test_schema_mirror_check_detects_drift(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    for schema_name in (
        "song-chart.schema.json",
        "provenance-record.schema.json",
        "recording-comparison.schema.json",
        "recording-comparison-metadata.schema.json",
    ):
        mirror.joinpath(schema_name).write_text("{}", encoding="utf-8")

    result = check_schema_mirror(mirror)

    assert result.dirty
    assert result.drift == (
        "song-chart.schema.json",
        "provenance-record.schema.json",
        "recording-comparison.schema.json",
        "recording-comparison-metadata.schema.json",
    )


def test_schema_mirror_sync_restores_canonical_resources(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    mirror.joinpath("song-chart.schema.json").write_text("{}", encoding="utf-8")

    sync_result = sync_schema_mirror(mirror)
    check_result = check_schema_mirror(mirror)

    assert sync_result.synced
    assert "song-chart.schema.json" in sync_result.drift
    assert check_result.clean
