import json
import importlib
import os
import secrets
import stat
import sys
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import chordatlas._fs as fs
import chordatlas.schema as schema
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


def _close_parent_then_fail(real_close, descriptor: int) -> None:
    is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
    real_close(descriptor)
    if is_directory:
        raise OSError("close failed")
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


def test_runtime_schema_validator_accepts_finite_additive_analysis() -> None:
    chart = SongChart(
        title="Test",
        analysis={"future": {"score": 0.75, "flags": [True, None, "stable"]}},
    )

    result = validate_chart_schema(chart)

    assert result.passed


def test_runtime_schema_validator_validates_decoded_tuple_analysis() -> None:
    chart = SongChart(title="Test", analysis={"roman": ("I", "V")})

    result = validate_chart_schema(chart)

    assert result.passed


def test_runtime_schema_validator_accepts_shared_json_aliases() -> None:
    shared = {"score": 0.75}
    chart = SongChart(title="Test", analysis={"future": [shared, shared]})

    result = validate_chart_schema(chart)

    assert result.passed


@pytest.mark.parametrize(
    ("analysis", "expected_error"),
    [
        ({"value": float("nan")}, "<root>: normalized chart is not strict JSON"),
        ({"value": float("inf")}, "<root>: normalized chart is not strict JSON"),
        ({"value": float("-inf")}, "<root>: normalized chart is not strict JSON"),
        (
            {"value": date(2026, 7, 22)},
            "<root>: normalized chart contains a value that is not JSON serializable",
        ),
        (
            {"value": b"private payload"},
            "<root>: normalized chart contains a value that is not JSON serializable",
        ),
        ({1: "value"}, "<root>: normalized chart JSON object keys must be strings"),
    ],
)
def test_runtime_schema_validator_rejects_non_json_analysis(
    analysis, expected_error
) -> None:
    result = validate_chart_schema(SongChart(title="Test", analysis=analysis))

    assert result.failed
    assert result.errors == (expected_error,)


def test_runtime_schema_validator_rejects_cyclic_analysis() -> None:
    cycle = []
    cycle.append(cycle)

    result = validate_chart_schema(
        SongChart(title="Test", analysis={"private payload": cycle})
    )

    assert result.failed
    assert result.errors == ("<root>: normalized chart is not strict JSON",)


@pytest.mark.parametrize("cyclic", [False, True])
def test_runtime_schema_validator_rejects_excessive_nesting(cyclic: bool) -> None:
    root = []
    cursor = root
    for _ in range(sys.getrecursionlimit() + 100):
        child = []
        cursor.append(child)
        cursor = child
    if cyclic:
        cursor.append(root)

    result = validate_chart_schema(SongChart(title="Test", analysis={"future": root}))

    assert result.failed
    assert result.errors == ("<root>: normalized chart exceeds JSON nesting limits",)


def test_runtime_schema_validator_preflights_before_optional_import(monkeypatch) -> None:
    def unexpected_import(name: str):
        raise AssertionError(f"optional import reached: {name}")

    monkeypatch.setattr("chordatlas.schema.importlib.import_module", unexpected_import)

    result = validate_chart_schema(
        SongChart(title="Test", analysis={"private payload": b"secret"})
    )

    assert result.failed
    assert result.errors == (
        "<root>: normalized chart contains a value that is not JSON serializable",
    )


def test_runtime_schema_validator_calls_to_mapping_once() -> None:
    class CountingChart:
        def __init__(self) -> None:
            self.calls = 0

        def to_mapping(self):
            self.calls += 1
            return SongChart.from_mapping({"title": "Test"}).to_mapping()

    chart = CountingChart()

    result = validate_chart_schema(chart)  # type: ignore[arg-type]

    assert result.passed
    assert chart.calls == 1


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


def test_schema_mirror_sync_repairs_non_utf8_mirror(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    mirror.joinpath("song-chart.schema.json").write_bytes(b"\xff\xfe\x00")

    check_result = check_schema_mirror(mirror)
    sync_result = sync_schema_mirror(mirror)

    assert "song-chart.schema.json" in check_result.drift
    assert "song-chart.schema.json" in sync_result.drift
    assert check_schema_mirror(mirror).clean


def test_schema_sync_reads_all_canonical_sources_before_writing(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    for name in schema.SCHEMA_NAMES:
        (mirror / name).write_text(f"original {name}\n", encoding="utf-8")
    original = {name: (mirror / name).read_bytes() for name in schema.SCHEMA_NAMES}
    real_schema_text = schema._schema_text

    def fail_second_source(name: str) -> str:
        if name == schema.PROVENANCE_SCHEMA:
            raise OSError("canonical read failed")
        return real_schema_text(name)

    monkeypatch.setattr(schema, "_schema_text", fail_second_source)

    with pytest.raises(OSError, match="canonical read failed"):
        sync_schema_mirror(mirror)

    assert {name: (mirror / name).read_bytes() for name in schema.SCHEMA_NAMES} == original


def test_schema_check_treats_canonical_symlink_as_drift(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    sync_schema_mirror(mirror)
    schema_path = mirror / schema.SONG_SCHEMA
    referent = tmp_path / "canonical.json"
    referent.write_text(schema_path.read_text(encoding="utf-8"), encoding="utf-8")
    schema_path.unlink()
    schema_path.symlink_to(referent)

    result = check_schema_mirror(mirror)

    assert result.dirty
    assert result.drift == (schema.SONG_SCHEMA,)


@pytest.mark.parametrize("link_kind", ["live", "dangling"])
def test_schema_sync_replaces_symlink_leaf_without_touching_referent(
    tmp_path, link_kind
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    referent = tmp_path / "referent.json"
    if link_kind == "live":
        referent.write_text("REFERENT\n", encoding="utf-8")
    schema_path.symlink_to(referent)

    sync_schema_mirror(mirror)

    assert not schema_path.is_symlink()
    assert schema_path.read_text(encoding="utf-8") == schema._schema_text(schema.SONG_SCHEMA)
    if link_kind == "live":
        assert referent.read_text(encoding="utf-8") == "REFERENT\n"
    else:
        assert not referent.exists()


def test_schema_sync_detaches_hard_link_and_preserves_peer(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    peer = tmp_path / "peer.json"
    peer.write_text("SHARED\n", encoding="utf-8")
    os.link(peer, schema_path)
    shared_inode = peer.stat().st_ino

    sync_schema_mirror(mirror)

    assert peer.read_text(encoding="utf-8") == "SHARED\n"
    assert peer.stat().st_ino == shared_inode
    assert schema_path.stat().st_ino != shared_inode
    assert schema_path.read_text(encoding="utf-8") == schema._schema_text(schema.SONG_SCHEMA)


def test_schema_sync_preserves_supported_regular_mode_bits(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    schema_path.write_text("ORIGINAL\n", encoding="utf-8")
    schema_path.chmod(0o4755)
    supported_mode = schema_path.stat().st_mode & 0o7777

    sync_schema_mirror(mirror)

    assert schema_path.stat().st_mode & 0o7777 == supported_mode


def test_schema_sync_new_files_honor_umask(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    previous_umask = os.umask(0o027)
    try:
        sync_schema_mirror(mirror)
    finally:
        os.umask(previous_umask)

    assert (mirror / schema.SONG_SCHEMA).stat().st_mode & 0o777 == 0o640


def test_schema_sync_rejects_directory_leaf_untouched(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    directory = mirror / schema.SONG_SCHEMA
    directory.mkdir()

    with pytest.raises(IsADirectoryError):
        sync_schema_mirror(mirror)

    assert directory.is_dir()
    assert list(directory.iterdir()) == []
    assert not (mirror / schema.PROVENANCE_SCHEMA).exists()


def test_schema_sync_failure_keeps_prior_updates_without_touching_later_files(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    for name in schema.SCHEMA_NAMES:
        (mirror / name).write_text(f"ORIGINAL {name}\n", encoding="utf-8")
    real_replace = fs._replace_text_at

    def fail_second(parent_fd, leaf, content, **kwargs):
        if leaf == schema.PROVENANCE_SCHEMA:
            raise OSError("second write failed")
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(fs, "_replace_text_at", fail_second)

    with pytest.raises(OSError, match="second write failed"):
        sync_schema_mirror(mirror)

    assert (mirror / schema.SONG_SCHEMA).read_text(encoding="utf-8") == schema._schema_text(
        schema.SONG_SCHEMA
    )
    for name in schema.SCHEMA_NAMES[1:]:
        assert (mirror / name).read_text(encoding="utf-8") == f"ORIGINAL {name}\n"


@pytest.mark.parametrize("matching_stage", [False, True])
def test_schema_sync_parent_retarget_stays_anchored(
    tmp_path, monkeypatch, matching_stage
) -> None:
    original = tmp_path / "original"
    retargeted = tmp_path / "retargeted"
    original.mkdir()
    retargeted.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(original, target_is_directory=True)
    real_replace = fs._replace_text_at
    first = True

    def retarget_then_replace(parent_fd, leaf, content, **kwargs):
        nonlocal first
        if first:
            first = False
            if matching_stage:
                (retargeted / ".chordatlas-schema-fixed.tmp").write_text(
                    "ATTACKER\n", encoding="utf-8"
                )
            linked.unlink()
            linked.symlink_to(retargeted, target_is_directory=True)
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(fs, "_replace_text_at", retarget_then_replace)
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    sync_schema_mirror(linked)

    for name in schema.SCHEMA_NAMES:
        assert (original / name).read_text(encoding="utf-8") == schema._schema_text(name)
        assert not (retargeted / name).exists()
    assert list(original.glob(".chordatlas-schema-*.tmp")) == []
    if matching_stage:
        attacker = retargeted / ".chordatlas-schema-fixed.tmp"
        assert attacker.read_text(encoding="utf-8") == "ATTACKER\n"


@pytest.mark.parametrize("failure_point", ["write", "flush", "close"])
def test_schema_sync_stage_failure_preserves_current_leaf(
    tmp_path, monkeypatch, failure_point
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    schema_path.write_text("ORIGINAL\n", encoding="utf-8")
    original_inode = schema_path.stat().st_ino

    class FailingStage:
        def __init__(self, staged: Path) -> None:
            self.staged = staged
            staged.write_text("", encoding="utf-8")

        def __enter__(self):
            return self

        def write(self, content: str) -> int:
            self.staged.write_text("PARTIAL", encoding="utf-8")
            if failure_point == "write":
                raise OSError("stage write failed")
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                raise OSError("stage flush failed")

        def fileno(self) -> int:
            return -1

        def __exit__(self, exc_type, exc, traceback) -> None:
            if failure_point == "close":
                raise OSError("stage close failed")

    monkeypatch.setattr(
        fs,
        "_open_stage",
        lambda parent_fd, staged, flags, reporter: FailingStage(mirror / staged),
    )
    monkeypatch.setattr(os, "fchmod", lambda descriptor, mode: None)

    with pytest.raises(OSError, match=f"stage {failure_point} failed"):
        sync_schema_mirror(mirror)

    assert schema_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert schema_path.stat().st_ino == original_inode
    assert list(mirror.glob(".chordatlas-schema-*.tmp")) == []


def test_schema_sync_replace_and_cleanup_failures_are_both_reported(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    schema_path.write_text("ORIGINAL\n", encoding="utf-8")
    real_unlink = os.unlink

    monkeypatch.setattr(
        os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    def fail_stage_unlink(path, *args, **kwargs):
        name = str(path)
        if name.startswith(".chordatlas-schema-") and not name.startswith(
            ".chordatlas-schema-mode-"
        ):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_stage_unlink)

    with pytest.raises(OSError, match="replace failed") as raised:
        sync_schema_mirror(mirror)

    assert raised.value.__notes__
    assert "cleanup denied" in raised.value.__notes__[0]
    assert schema_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert len(list(mirror.glob(".chordatlas-schema-*.tmp"))) == 1


def test_schema_sync_reports_retained_permission_probe(tmp_path, monkeypatch) -> None:
    mirror = tmp_path / "schemas"
    real_unlink = os.unlink

    def fail_probe_unlink(path, *args, **kwargs):
        if str(path).startswith(".chordatlas-schema-mode-"):
            raise PermissionError("probe cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_probe_unlink)

    with pytest.raises(
        OSError,
        match=(
            r"Temporary cleanup failed: temporary file "
            r"\.chordatlas-schema-mode-.*: probe cleanup denied"
        ),
    ):
        sync_schema_mirror(mirror)

    probes = list(mirror.glob(".chordatlas-schema-mode-*.tmp"))
    assert len(probes) == 1
    assert probes[0].read_bytes() == b""
    assert not any((mirror / name).exists() for name in schema.SCHEMA_NAMES)


def test_schema_sync_parent_close_failure_reports_published_batch(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)

    with pytest.raises(fs.PublishedCleanupError, match="close failed"):
        sync_schema_mirror(mirror)

    assert check_schema_mirror(mirror).clean


def test_schema_sync_replace_interrupt_propagates_and_cleans_stage(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)

    with pytest.raises(KeyboardInterrupt):
        sync_schema_mirror(mirror)

    assert not (mirror / schema.SONG_SCHEMA).exists()
    assert list(mirror.glob(".chordatlas-schema-*.tmp")) == []


def test_schema_sync_works_in_searchable_unreadable_parent(tmp_path) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    for name in schema.SCHEMA_NAMES:
        (mirror / name).write_text("ORIGINAL\n", encoding="utf-8")
    mirror.chmod(0o300)
    try:
        try:
            list(mirror.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("directory remains readable under the test permission mode")
        sync_schema_mirror(mirror)
    finally:
        mirror.chmod(0o700)

    assert check_schema_mirror(mirror).clean


def test_schema_sync_staging_collision_exhaustion_preserves_mirror(
    tmp_path, monkeypatch
) -> None:
    mirror = tmp_path / "schemas"
    mirror.mkdir()
    schema_path = mirror / schema.SONG_SCHEMA
    schema_path.write_text("ORIGINAL\n", encoding="utf-8")
    staged = mirror / ".chordatlas-schema-fixed.tmp"
    staged.write_text("OCCUPIED\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    with pytest.raises(OSError, match="unable to allocate a private staging file"):
        sync_schema_mirror(mirror)

    assert schema_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert staged.read_text(encoding="utf-8") == "OCCUPIED\n"
