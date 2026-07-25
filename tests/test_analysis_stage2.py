from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import stat
import struct
import threading
import time
import wave
from pathlib import Path

import pytest

import chordatlas.analysis.baseline as baseline
import chordatlas.analysis.store as analysis_store
from chordatlas.analysis import (
    AnalysisConfig,
    AnalysisError,
    AnalysisService,
    AnalysisSpec,
    AnalysisStore,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    EngineRef,
    ReferenceSegment,
    analyze_pcm16_wav,
    evaluate_timeline,
)
from chordatlas.analysis.models import (
    content_digest,
    state_from_mapping,
    timeline_from_mapping,
)
from chordatlas.media import FrameRange, ProjectMediaStore, Timebase


CHORDS = {
    "C:maj": (261.63, 329.63, 392.00),
    "G:maj": (196.00, 246.94, 293.66),
    "A:min": (220.00, 261.63, 329.63),
    "F:maj": (174.61, 220.00, 261.63),
}


def write_progression(
    path: Path,
    *,
    sample_rate: int = 8_000,
    seconds_per_chord: int = 2,
    labels: tuple[str, ...] = ("C:maj", "G:maj", "A:min", "F:maj"),
) -> tuple[Timebase, tuple[ReferenceSegment, ...]]:
    values = []
    frames_per_chord = sample_rate * seconds_per_chord
    for chord_index, label in enumerate(labels):
        for local in range(frames_per_chord):
            absolute = chord_index * frames_per_chord + local
            pulse = 1.0 if local % (sample_rate // 2) < sample_rate // 100 else 0.65
            sample = sum(
                math.sin(2 * math.pi * frequency * absolute / sample_rate)
                for frequency in CHORDS[label]
            )
            values.append(round(sample * 4_500 * pulse))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(struct.pack(f"<{len(values)}h", *values))
    reference = tuple(
        ReferenceSegment(
            FrameRange(index * frames_per_chord, (index + 1) * frames_per_chord),
            label,
        )
        for index, label in enumerate(labels)
    )
    return Timebase(sample_rate, len(values)), reference


def spec_for(path: Path) -> tuple[AnalysisSpec, tuple[ReferenceSegment, ...]]:
    timebase, reference = write_progression(path)
    return (
        AnalysisSpec.create(
            asset_id=f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            timebase=timebase,
            analyzed_range=FrameRange(0, timebase.duration_frames),
            config=AnalysisConfig.baseline(),
        ),
        reference,
    )


def wait_terminal(service: AnalysisService, run_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.status(run_id)
        if status["status"] in {"succeeded", "failed", "cancelled"}:
            return status
        time.sleep(0.05)
    raise AssertionError("analysis did not reach a terminal state")


def overwrite_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(path, 0o600)


def test_spec_digest_is_canonical_and_changes_with_reproducibility_inputs() -> None:
    timebase = Timebase(8_000, 80_000)
    frame_range = FrameRange(0, 80_000)
    first = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=frame_range,
    )
    second = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=frame_range,
    )
    changed = AnalysisSpec.create(
        asset_id="sha256:" + ("b" * 64),
        timebase=timebase,
        analyzed_range=frame_range,
    )

    assert first.id == second.id
    assert first.id != changed.id
    assert content_digest({"a": 1, "b": 2}) == content_digest({"b": 2, "a": 1})
    assert "created_at" not in json.dumps(first.identity_mapping())


def test_hashed_contract_rejects_float_parameters() -> None:
    with pytest.raises((TypeError, ValueError)):
        AnalysisConfig(EngineRef(), (("target_rate", 2048.0),))  # type: ignore[arg-type]


def test_baseline_produces_reproducible_genuine_hypotheses(tmp_path: Path) -> None:
    path = tmp_path / "progression.wav"
    spec, reference = spec_for(path)

    first = analyze_pcm16_wav(path, spec)
    second = analyze_pcm16_wav(path, spec)
    labels = [segment.candidates[0].canonical_symbol for segment in first.segments]

    assert first.id == second.id
    assert labels == ["C:maj", "G:maj", "A:min", "F:maj"]
    assert first.tempo_hypotheses
    assert 110_000 <= first.tempo_hypotheses[0].bpm_milli <= 130_000
    assert first.beats
    assert first.key_hypotheses
    assert first.timebase == spec.timebase
    assert first.analyzed_range == spec.analyzed_range
    assert all(segment.state == "chord" for segment in first.segments)
    metrics = evaluate_timeline(first, reference)
    assert metrics["primary_duration_accuracy_ppm"] >= 800_000
    assert metrics["scope"] == "deterministic_synthetic_fixture_only"


@pytest.mark.parametrize("sample", [0, 1])
def test_baseline_emits_no_chord_for_silent_or_low_energy_pcm16(
    tmp_path: Path,
    sample: int,
) -> None:
    path = tmp_path / f"quiet-{sample}.wav"
    sample_rate = 8_000
    values = [sample] * (sample_rate * 2)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(struct.pack(f"<{len(values)}h", *values))
    timebase = Timebase(sample_rate, len(values))
    spec = AnalysisSpec.create(
        asset_id=f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
        timebase=timebase,
        analyzed_range=FrameRange(0, timebase.duration_frames),
    )

    timeline = analyze_pcm16_wav(path, spec)

    assert timeline.result_kind == "candidates"
    assert timeline.segments
    assert all(segment.state == "no_chord" for segment in timeline.segments)
    assert all(
        segment.candidates[0].canonical_symbol == "N.C."
        for segment in timeline.segments
    )


def test_path_baseline_rejects_media_identity_mismatch(tmp_path: Path) -> None:
    expected_path = tmp_path / "expected.wav"
    spec, _reference = spec_for(expected_path)
    replacement_path = tmp_path / "replacement.wav"
    replacement_timebase, _ = write_progression(
        replacement_path,
        labels=("G:maj", "G:maj", "G:maj", "G:maj"),
    )
    assert replacement_timebase == spec.timebase

    with pytest.raises(AnalysisError) as mismatch:
        analyze_pcm16_wav(replacement_path, spec)

    assert mismatch.value.code == "media_identity_mismatch"
    assert mismatch.value.retryable is False


def test_path_baseline_bounds_immutable_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.wav"
    spec, _reference = spec_for(path)
    monkeypatch.setattr(baseline, "_MAX_PATH_INPUT_BYTES", len(path.read_bytes()) - 1)

    with pytest.raises(AnalysisError) as oversized:
        analyze_pcm16_wav(path, spec)

    assert oversized.value.code == "media_too_large"
    assert oversized.value.retryable is False


def test_path_baseline_analyzes_hashed_snapshot_during_transient_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.wav"
    spec, _reference = spec_for(path)
    original_bytes = path.read_bytes()
    replacement = tmp_path / "replacement.wav"
    write_progression(
        replacement,
        labels=("G:maj", "G:maj", "G:maj", "G:maj"),
    )
    replacement_bytes = replacement.read_bytes()
    analyze_stream = baseline.analyze_pcm16_wav_stream

    def mutate_after_decode(handle, selected, *, cancelled):
        with path.open("r+b") as changed:
            changed.seek(0)
            changed.write(replacement_bytes)
            changed.truncate()
        try:
            return analyze_stream(handle, selected, cancelled=cancelled)
        finally:
            with path.open("r+b") as restored:
                restored.seek(0)
                restored.write(original_bytes)
                restored.truncate()

    monkeypatch.setattr(baseline, "analyze_pcm16_wav_stream", mutate_after_decode)

    timeline = analyze_pcm16_wav(path, spec)

    assert [
        segment.candidates[0].canonical_symbol for segment in timeline.segments
    ] == ["C:maj", "G:maj", "A:min", "F:maj"]


@pytest.mark.parametrize(
    ("state", "label", "message"),
    [
        ("chord", "N.C.", "cannot contain"),
        ("no_chord", "C:maj", "requires no-chord"),
    ],
)
def test_candidate_segment_state_matches_candidate_semantics(
    state: str,
    label: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ChordSegment(
            0,
            FrameRange(0, 1_000),
            state,
            (ChordCandidate(label, label, 1, 900_000),),
            900_000 if state == "no_chord" else 0,
        )


@pytest.mark.parametrize(
    ("sample_rate", "duration_frames"),
    [(16_000, 64_000), (8_000, 63_999)],
)
def test_path_baseline_rejects_spec_timebase_mismatch(
    tmp_path: Path,
    sample_rate: int,
    duration_frames: int,
) -> None:
    path = tmp_path / "source.wav"
    actual_timebase, _reference = write_progression(path)
    spec = AnalysisSpec.create(
        asset_id=f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
        timebase=Timebase(sample_rate, duration_frames),
        analyzed_range=FrameRange(0, min(duration_frames, actual_timebase.duration_frames)),
    )

    with pytest.raises(AnalysisError) as mismatch:
        analyze_pcm16_wav(path, spec)

    assert mismatch.value.code == "media_timebase_mismatch"
    assert mismatch.value.retryable is False


def test_analysis_reopens_and_verifies_exact_media_before_worker_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.wav"
    timebase, _reference = write_progression(source_path)
    payload = source_path.read_bytes()
    media = ProjectMediaStore.initialize(tmp_path)
    with source_path.open("rb") as handle:
        source, asset = media.import_stream(
            handle,
            byte_length=len(payload),
            display_name="source.wav",
            authorization_confirmed=True,
        )
    service = AnalysisService(tmp_path)
    entered = threading.Event()
    proceed = threading.Event()
    original_supervise = service._supervise

    def delayed(job, source_id, spec) -> None:
        entered.set()
        assert proceed.wait(5)
        original_supervise(job, source_id, spec)

    monkeypatch.setattr(service, "_supervise", delayed)
    run_id = service.start(source_id=source.id)
    assert entered.wait(5)

    replacement = tmp_path / "replacement.wav"
    replacement_timebase, _ = write_progression(
        replacement,
        labels=("G:maj", "G:maj", "G:maj", "G:maj"),
    )
    assert replacement_timebase == timebase == asset.timebase
    blob = media.audio_path_for_source(source.id)
    os.replace(replacement, blob)
    os.chmod(blob, 0o600)
    proceed.set()

    terminal = wait_terminal(service, run_id)
    service.close()
    assert terminal["status"] == "failed"
    assert terminal["failure_code"] == "media_unavailable"
    with pytest.raises(AnalysisError):
        service.timeline(run_id)


def test_evaluation_weights_exact_interval_intersections() -> None:
    timebase = Timebase(100, 100)
    spec_id = "sha256:" + ("a" * 64)

    def segment(ordinal: int, start: int, end: int, label: str) -> ChordSegment:
        return ChordSegment(
            ordinal,
            FrameRange(start, end),
            "chord",
            (ChordCandidate(label, label, 1, 1_000_000),),
            0,
        )

    timeline = ChordCandidateTimeline.create(
        spec_id=spec_id,
        timebase=timebase,
        analyzed_range=FrameRange(0, 100),
        result_kind="candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            segment(0, 0, 49, "G:maj"),
            segment(1, 49, 51, "C:maj"),
            segment(2, 51, 100, "G:maj"),
        ),
        engine=EngineRef(),
    )

    metrics = evaluate_timeline(
        timeline,
        (ReferenceSegment(FrameRange(0, 100), "C:maj"),),
    )

    assert metrics["primary_duration_accuracy_ppm"] == 20_000
    assert metrics["top_k_duration_accuracy_ppm"] == 20_000


def test_evaluation_does_not_double_count_compatible_merge_as_boundary_move() -> None:
    timebase = Timebase(100, 100)
    spec_id = "sha256:" + ("a" * 64)

    def segment(ordinal: int, start: int, end: int, label: str) -> ChordSegment:
        return ChordSegment(
            ordinal,
            FrameRange(start, end),
            "chord",
            (ChordCandidate(label, label, 1, 1_000_000),),
            0,
        )

    timeline = ChordCandidateTimeline.create(
        spec_id=spec_id,
        timebase=timebase,
        analyzed_range=FrameRange(0, 100),
        result_kind="candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            segment(0, 0, 10, "C:maj"),
            segment(1, 10, 50, "C:maj"),
            segment(2, 50, 100, "G:maj"),
        ),
        engine=EngineRef(),
    )
    metrics = evaluate_timeline(
        timeline,
        (
            ReferenceSegment(FrameRange(0, 50), "C:maj"),
            ReferenceSegment(FrameRange(50, 100), "G:maj"),
        ),
    )

    assert metrics["boundary_mean_absolute_error_frames"] == 0
    assert metrics["heuristic_correction_flags"]["boundary_move"] == 0
    assert metrics["heuristic_correction_flags"]["merge"] == 1


def test_evaluation_does_not_turn_relabel_into_boundary_move() -> None:
    timebase = Timebase(100, 100)
    timeline = ChordCandidateTimeline.create(
        spec_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=FrameRange(0, 100),
        result_kind="candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            ChordSegment(
                0,
                FrameRange(0, 50),
                "chord",
                (ChordCandidate("D:maj", "D:maj", 1, 1_000_000),),
                0,
            ),
            ChordSegment(
                1,
                FrameRange(50, 100),
                "chord",
                (ChordCandidate("G:maj", "G:maj", 1, 1_000_000),),
                0,
            ),
        ),
        engine=EngineRef(),
    )
    metrics = evaluate_timeline(
        timeline,
        (
            ReferenceSegment(FrameRange(0, 50), "C:maj"),
            ReferenceSegment(FrameRange(50, 100), "G:maj"),
        ),
    )

    assert metrics["boundary_mean_absolute_error_frames"] == 0
    assert metrics["boundary_unmatched_count"] == 0
    assert metrics["heuristic_correction_flags"]["relabel"] == 1
    assert metrics["heuristic_correction_flags"]["boundary_move"] == 0


def test_timeline_requires_explicit_contiguous_coverage() -> None:
    timebase = Timebase(8_000, 8_000)
    spec_id = "sha256:" + ("a" * 64)
    candidate = ChordCandidate("C:maj", "C:maj", 1, 700_000)

    with pytest.raises(ValueError, match="gap-explicit"):
        ChordCandidateTimeline.create(
            spec_id=spec_id,
            timebase=timebase,
            analyzed_range=FrameRange(0, 8_000),
            result_kind="candidates",
            beats=(),
            tempo_hypotheses=(),
            key_hypotheses=(),
            segments=(
                ChordSegment(0, FrameRange(0, 2_000), "chord", (candidate,), 0),
                ChordSegment(1, FrameRange(3_000, 8_000), "chord", (candidate,), 0),
            ),
            engine=EngineRef(),
        )


def test_store_preserves_attempts_and_publishes_success_last(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    timebase = Timebase(8_000, 8_000)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=FrameRange(0, 8_000),
    )
    first = store.create_run(spec, source_id="src_" + ("a" * 32))
    second = store.create_run(spec, source_id="src_" + ("a" * 32), retry_of=first.id)

    assert first.id != second.id
    assert store.load_state(first.id).status == "queued"
    store.transition(first.id, "running")
    store.transition(
        first.id,
        "failed",
        failure_code="fixture_failure",
        failure_message="Synthetic failure.",
        retryable=True,
    )
    assert store.load_state(first.id).status == "failed"
    assert store.load_state(second.id).status == "queued"
    assert not (store.runs / first.id / "output.json").exists()


@pytest.mark.parametrize(
    ("record", "mutation"),
    [
        ("run", lambda value: value.update({"analysis_run_schema_version": "9"})),
        ("run", lambda value: value.update({"unexpected": True})),
        ("run", lambda value: value.update({"created_at": 1})),
        ("state", lambda value: value.update({"run_state_schema_version": "9"})),
        ("state", lambda value: value.update({"unexpected": True})),
        ("state", lambda value: value.update({"revision": True})),
        ("state", lambda value: value.update({"revision": 0.5})),
        ("state", lambda value: value.update({"retryable": 0})),
    ],
)
def test_run_records_require_exact_schema_keys_and_types(
    tmp_path: Path,
    record: str,
    mutation,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    if record == "run":
        path = store.runs / run.id / "request.json"
        loader = lambda: store.load_run(run.id)
    else:
        path = store.runs / run.id / "events" / "00000000.json"
        loader = lambda: store.load_state(run.id)
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    overwrite_private_json(path, value)

    with pytest.raises(AnalysisError) as rejected:
        loader()

    assert rejected.value.code == "analysis_storage_integrity"


def test_run_and_state_records_are_bound_to_requested_paths(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    first = store.create_run(spec, source_id="src_" + ("a" * 32))
    second = store.create_run(spec, source_id="src_" + ("b" * 32))
    first_request = store.runs / first.id / "request.json"
    second_request = store.runs / second.id / "request.json"
    first_event = store.runs / first.id / "events" / "00000000.json"
    second_event = store.runs / second.id / "events" / "00000000.json"

    first_request.write_bytes(second_request.read_bytes())
    with pytest.raises(AnalysisError) as run_rejected:
        store.load_run(first.id)

    first_event.write_bytes(second_event.read_bytes())
    with pytest.raises(AnalysisError) as state_rejected:
        store.load_state(first.id)

    assert run_rejected.value.code == "analysis_storage_integrity"
    assert state_rejected.value.code == "analysis_storage_integrity"


def test_state_event_revision_is_bound_to_filename(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    event = store.runs / run.id / "events" / "00000000.json"
    value = json.loads(event.read_text(encoding="utf-8"))
    value["revision"] = 1
    overwrite_private_json(event, value)

    with pytest.raises(AnalysisError) as rejected:
        store.load_state(run.id)

    assert rejected.value.code == "analysis_storage_integrity"


def test_spec_and_timeline_records_require_exact_contract_and_path_identity(
    tmp_path: Path,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    first = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    second = AnalysisSpec.create(
        asset_id="sha256:" + ("b" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    store.publish_spec(first)
    store.publish_spec(second)
    first_path = store._digest_path(store.specs, first.id)
    second_path = store._digest_path(store.specs, second.id)
    first_payload = first_path.read_bytes()
    first_path.write_bytes(second_path.read_bytes())

    with pytest.raises(AnalysisError) as spec_rejected:
        store.load_spec(first.id)
    first_path.write_bytes(first_payload)

    first_timeline = ChordCandidateTimeline.create(
        spec_id=first.id,
        timebase=first.timebase,
        analyzed_range=first.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=first.config.engine,
    )
    second_timeline = ChordCandidateTimeline.create(
        spec_id=second.id,
        timebase=second.timebase,
        analyzed_range=second.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=second.config.engine,
    )
    first_timeline_path = store._digest_path(
        store.timelines,
        first_timeline.id,
        create=True,
    )
    second_timeline_path = store._digest_path(
        store.timelines,
        second_timeline.id,
        create=True,
    )
    store._publish_immutable(first_timeline_path, first_timeline.to_record_mapping())
    store._publish_immutable(second_timeline_path, second_timeline.to_record_mapping())
    run = store.create_run(first, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    store._publish_immutable(
        store.runs / run.id / "output.json",
        {"timeline_id": first_timeline.id},
    )
    store.transition(run.id, "succeeded", timeline_id=first_timeline.id)
    first_timeline_path.write_bytes(second_timeline_path.read_bytes())

    with pytest.raises(AnalysisError) as timeline_rejected:
        store.timeline_for_run(run.id)

    assert spec_rejected.value.code == "analysis_storage_integrity"
    assert timeline_rejected.value.code == "analysis_storage_integrity"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"analysis_spec_schema_version": "9"}),
        lambda value: value.update({"unexpected": True}),
        lambda value: value["timebase"].update({"sample_rate": True}),
        lambda value: value["analyzed_range"].update({"start_frame": 0.5}),
        lambda value: value["config"].update({"random_seed": True}),
        lambda value: value["config"].update({"unexpected": True}),
    ],
)
def test_spec_reader_rejects_schema_key_and_integer_drift(
    tmp_path: Path,
    mutation,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    store.publish_spec(spec)
    path = store._digest_path(store.specs, spec.id)
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    overwrite_private_json(path, value)

    with pytest.raises(AnalysisError) as rejected:
        store.load_spec(spec.id)

    assert rejected.value.code == "analysis_storage_integrity"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"candidate_timeline_schema_version": "9"}),
        lambda value: value.update({"unexpected": True}),
        lambda value: value.update({"beats": [True]}),
        lambda value: value["engine"].update({"unexpected": True}),
    ],
)
def test_timeline_reader_rejects_schema_key_and_integer_drift(
    mutation,
) -> None:
    timeline = ChordCandidateTimeline.create(
        spec_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=EngineRef(),
    )
    value = timeline.to_record_mapping()
    mutation(value)

    with pytest.raises((TypeError, ValueError)):
        timeline_from_mapping(value)


@pytest.mark.parametrize(
    "payload",
    [
        '{"id":"first","id":"second"}',
        '{"value":0.5}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ],
)
def test_analysis_json_reader_rejects_duplicate_and_nonfinite_values(
    tmp_path: Path,
    payload: str,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    path = store.tmp / "malformed.json"
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, 0o600)

    with pytest.raises(AnalysisError) as rejected:
        store._read_json(path)

    assert rejected.value.code == "analysis_storage_integrity"


def test_state_cache_repairs_from_immutable_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    original = store._replace_state_at
    calls = 0

    def fail_once(
        run_descriptor: int,
        state,
        *,
        identity_guard=None,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic cache write failure")
        original(
            run_descriptor,
            state,
            identity_guard=identity_guard,
        )

    monkeypatch.setattr(store, "_replace_state_at", fail_once)
    with pytest.raises(OSError, match="synthetic"):
        store.transition(run.id, "running")

    assert store.load_state(run.id).status == "running"
    assert calls == 2


def test_malformed_state_cache_repairs_from_immutable_event(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    state_path = store.runs / run.id / "state.json"
    state_path.write_text("{malformed", encoding="utf-8")
    os.chmod(state_path, 0o600)

    assert store.load_state(run.id).status == "queued"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "queued"


def test_invalid_state_cache_repairs_from_immutable_event(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    expected = store.load_state(run.id)
    state_path = store.runs / run.id / "state.json"
    invalid = expected.to_record_mapping()
    invalid.pop("updated_at")
    overwrite_private_json(state_path, invalid)

    assert store.load_state(run.id) == expected
    assert json.loads(state_path.read_text(encoding="utf-8")) == expected.to_record_mapping()


def test_state_replay_requires_complete_canonical_event_names(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    events = store.runs / run.id / "events"
    (events / "00000001.json").rename(events / "00000002.json")

    with pytest.raises(AnalysisError) as gap:
        store.load_state(run.id)

    (events / "00000002.json").rename(events / "00000001.json")
    (events / "unexpected.json").write_text("{}\n", encoding="utf-8")
    os.chmod(events / "unexpected.json", 0o600)

    with pytest.raises(AnalysisError) as unexpected:
        store.load_state(run.id)

    assert gap.value.code == "analysis_storage_integrity"
    assert unexpected.value.code == "analysis_storage_integrity"


def test_state_replay_requires_queued_revision_zero(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    event = store.runs / run.id / "events" / "00000000.json"
    value = json.loads(event.read_text(encoding="utf-8"))
    value["status"] = "running"
    overwrite_private_json(event, value)

    with pytest.raises(AnalysisError) as rejected:
        store.load_state(run.id)

    assert rejected.value.code == "analysis_storage_integrity"


def test_state_replay_rejects_invalid_edge_and_terminal_descendant(
    tmp_path: Path,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    invalid = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(invalid.id, "running")
    invalid_event = store.runs / invalid.id / "events" / "00000001.json"
    invalid_value = json.loads(invalid_event.read_text(encoding="utf-8"))
    invalid_value["status"] = "queued"
    overwrite_private_json(invalid_event, invalid_value)

    with pytest.raises(AnalysisError) as invalid_edge:
        store.load_state(invalid.id)

    terminal = store.create_run(spec, source_id="src_" + ("b" * 32))
    failed = store.transition(
        terminal.id,
        "failed",
        failure_code="fixture_failure",
        failure_message="Synthetic failure.",
        retryable=True,
    )
    descendant = failed.to_record_mapping()
    descendant.update(
        {
            "revision": 2,
            "status": "running",
            "failure_code": None,
            "failure_message": None,
            "retryable": False,
        }
    )
    descendant_path = store.runs / terminal.id / "events" / "00000002.json"
    overwrite_private_json(descendant_path, descendant)

    with pytest.raises(AnalysisError) as terminal_descendant:
        store.load_state(terminal.id)

    assert invalid_edge.value.code == "analysis_storage_integrity"
    assert terminal_descendant.value.code == "analysis_storage_integrity"


def test_state_replay_detects_tampering_before_valid_tail(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    store.transition(
        run.id,
        "failed",
        failure_code="fixture_failure",
        failure_message="Synthetic failure.",
        retryable=True,
    )
    earlier = store.runs / run.id / "events" / "00000001.json"
    value = json.loads(earlier.read_text(encoding="utf-8"))
    value["status"] = "cancel_requested"
    overwrite_private_json(earlier, value)

    with pytest.raises(AnalysisError) as rejected:
        store.load_state(run.id)

    assert rejected.value.code == "analysis_storage_integrity"


@pytest.mark.parametrize("terminal_status", ("failed", "succeeded"))
def test_state_replay_serializes_cross_instance_terminal_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    reader = AnalysisStore.initialize(tmp_path)
    writer = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = writer.create_run(spec, source_id="src_" + ("a" * 32))
    writer.transition(run.id, "running")
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=spec.config.engine,
    )
    snapshot_taken = threading.Event()
    release_reader = threading.Event()
    writer_waiting = threading.Event()
    original_names = reader._event_names
    original_flock = analysis_store.fcntl.flock

    def pause_after_snapshot(events_descriptor: int) -> tuple[str, ...]:
        names = original_names(events_descriptor)
        if threading.current_thread().name == "state-reader":
            snapshot_taken.set()
            assert release_reader.wait(5)
        return names

    def observe_writer_lock(descriptor: int, operation: int) -> None:
        if (
            threading.current_thread().name == "state-writer"
            and operation == fcntl.LOCK_EX
        ):
            writer_waiting.set()
        original_flock(descriptor, operation)

    monkeypatch.setattr(reader, "_event_names", pause_after_snapshot)
    monkeypatch.setattr(analysis_store.fcntl, "flock", observe_writer_lock)
    observed: dict[str, object] = {}

    def read_state() -> None:
        observed["reader"] = reader.load_state(run.id)

    def publish_terminal() -> None:
        if terminal_status == "succeeded":
            observed["writer"] = writer.publish_success(run.id, timeline)
        else:
            observed["writer"] = writer.transition(
                run.id,
                "failed",
                failure_code="fixture_failure",
                failure_message="Synthetic failure.",
                retryable=True,
            )

    reader_thread = threading.Thread(target=read_state, name="state-reader")
    writer_thread = threading.Thread(target=publish_terminal, name="state-writer")
    reader_thread.start()
    assert snapshot_taken.wait(5)
    run_dir = writer.runs / run.id
    with writer.anchor.directory(writer.anchor.relative(run_dir)) as probe:
        with pytest.raises(BlockingIOError):
            original_flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    writer_thread.start()
    assert writer_waiting.wait(5)
    assert not (writer.runs / run.id / "events" / "00000002.json").exists()

    release_reader.set()
    reader_thread.join(5)
    writer_thread.join(5)

    assert not reader_thread.is_alive()
    assert not writer_thread.is_alive()
    assert observed["reader"].revision == 1
    assert observed["reader"].status == "running"
    assert observed["writer"].revision == 2
    assert observed["writer"].status == terminal_status
    assert reader.load_state(run.id) == observed["writer"]
    cache = json.loads((writer.runs / run.id / "state.json").read_text(encoding="utf-8"))
    assert (cache["revision"], cache["status"]) == (2, terminal_status)
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


def test_success_publication_is_one_cross_instance_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    publisher = AnalysisStore.initialize(tmp_path)
    contender = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = publisher.create_run(spec, source_id="src_" + ("a" * 32))
    publisher.transition(run.id, "running")
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=spec.config.engine,
    )
    output_pending = threading.Event()
    release_publisher = threading.Event()
    contender_waiting = threading.Event()
    original_publish = publisher._publish_immutable
    original_flock = analysis_store.fcntl.flock

    def pause_before_output(path: Path, value: dict) -> None:
        if path.name == "output.json":
            output_pending.set()
            assert release_publisher.wait(5)
        original_publish(path, value)

    def observe_contender(descriptor: int, operation: int) -> None:
        if (
            threading.current_thread().name == "failure-contender"
            and operation == fcntl.LOCK_EX
        ):
            contender_waiting.set()
        original_flock(descriptor, operation)

    monkeypatch.setattr(publisher, "_publish_immutable", pause_before_output)
    monkeypatch.setattr(analysis_store.fcntl, "flock", observe_contender)
    observed: dict[str, object] = {}

    def publish() -> None:
        observed["published"] = publisher.publish_success(run.id, timeline)

    def fail() -> None:
        try:
            contender.transition(
                run.id,
                "failed",
                failure_code="fixture_failure",
                failure_message="Synthetic failure.",
                retryable=True,
            )
        except AnalysisError as error:
            observed["contender_error"] = error

    publisher_thread = threading.Thread(target=publish, name="success-publisher")
    contender_thread = threading.Thread(target=fail, name="failure-contender")
    publisher_thread.start()
    assert output_pending.wait(5)
    contender_thread.start()
    assert contender_waiting.wait(5)
    assert not (publisher.runs / run.id / "output.json").exists()
    assert not (publisher.runs / run.id / "events" / "00000002.json").exists()

    release_publisher.set()
    publisher_thread.join(5)
    contender_thread.join(5)

    assert not publisher_thread.is_alive()
    assert not contender_thread.is_alive()
    assert observed["published"].status == "succeeded"
    assert observed["contender_error"].code == "invalid_run_transition"
    assert publisher.load_state(run.id).status == "succeeded"
    assert publisher.timeline_for_run(run.id) == timeline


@pytest.mark.parametrize("terminal_status", ("failed", "cancelled", "succeeded"))
@pytest.mark.parametrize(
    "replacement_boundary",
    ("event_publish", "cache_prepublish", "cache_postpublish"),
)
def test_event_directory_replacement_fails_before_terminal_cache_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    replacement_boundary: str,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    if terminal_status == "cancelled":
        store.transition(run.id, "cancel_requested")
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=spec.config.engine,
    )
    run_dir = store.runs / run.id
    events = run_dir / "events"
    displaced = store.tmp / f"displaced-events-{terminal_status}"
    cached_before = (run_dir / "state.json").read_bytes()
    original_event_publish = store._publish_event
    original_cache_publish = store._replace_state_at
    replaced = False

    def replace_events() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        events.rename(displaced)
        events.mkdir(mode=0o700)

    def replace_before_event_publish(
        events_descriptor: int,
        revision: int,
        value: dict,
    ) -> None:
        replace_events()
        original_event_publish(events_descriptor, revision, value)

    def replace_at_cache_publish(
        run_descriptor: int,
        state,
        *,
        identity_guard=None,
    ) -> None:
        if replacement_boundary == "cache_prepublish":
            replace_events()
        original_cache_publish(
            run_descriptor,
            state,
            identity_guard=identity_guard,
        )
        if replacement_boundary == "cache_postpublish":
            replace_events()

    if replacement_boundary == "event_publish":
        monkeypatch.setattr(store, "_publish_event", replace_before_event_publish)
    else:
        monkeypatch.setattr(store, "_replace_state_at", replace_at_cache_publish)

    with pytest.raises(AnalysisError) as rejected:
        if terminal_status == "succeeded":
            store.publish_success(run.id, timeline)
        else:
            store.transition(
                run.id,
                terminal_status,
                failure_code="fixture_failure" if terminal_status == "failed" else None,
                failure_message="Synthetic failure." if terminal_status == "failed" else None,
                retryable=terminal_status == "failed",
            )

    assert rejected.value.code == "analysis_storage_integrity"
    assert replaced
    assert not list(events.iterdir())
    assert (run_dir / "state.json").read_bytes() == cached_before
    assert any(path.name.endswith(".json") for path in displaced.iterdir())
    if terminal_status == "succeeded":
        assert (run_dir / "output.json").exists()
    with pytest.raises(AnalysisError) as replay_rejected:
        store.load_state(run.id)
    assert replay_rejected.value.code == "analysis_storage_integrity"


@pytest.mark.parametrize(
    "replacement_boundary",
    ("event_publish", "cache_prepublish", "cache_postpublish"),
)
def test_event_directory_replacement_fails_revision_zero_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_boundary: str,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    displaced = store.tmp / "displaced-revision-zero-events"
    original_event_publish = store._publish_event
    original_cache_publish = store._replace_state_at
    captured: dict[str, Path] = {}
    replaced = False

    def replace_events() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        run_dir = next(path for path in store.runs.iterdir() if path.is_dir())
        events = run_dir / "events"
        events.rename(displaced)
        events.mkdir(mode=0o700)
        captured["run_dir"] = run_dir

    def replace_before_event_publish(
        events_descriptor: int,
        revision: int,
        value: dict,
    ) -> None:
        assert revision == 0
        replace_events()
        original_event_publish(events_descriptor, revision, value)

    def replace_at_cache_publish(
        run_descriptor: int,
        state,
        *,
        identity_guard=None,
    ) -> None:
        if replacement_boundary == "cache_prepublish":
            replace_events()
        original_cache_publish(
            run_descriptor,
            state,
            identity_guard=identity_guard,
        )
        if replacement_boundary == "cache_postpublish":
            replace_events()

    if replacement_boundary == "event_publish":
        monkeypatch.setattr(store, "_publish_event", replace_before_event_publish)
    else:
        monkeypatch.setattr(store, "_replace_state_at", replace_at_cache_publish)

    with pytest.raises(AnalysisError) as rejected:
        store.create_run(spec, source_id="src_" + ("a" * 32))

    assert rejected.value.code == "analysis_storage_integrity"
    run_dir = captured["run_dir"]
    assert not list((run_dir / "events").iterdir())
    assert not (run_dir / "state.json").exists()
    assert [path.name for path in displaced.iterdir()] == ["00000000.json"]


@pytest.mark.parametrize(
    "replacement_boundary",
    ("event_publish", "cache_prepublish", "cache_postpublish"),
)
def test_recovery_rejects_event_directory_replacement_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_boundary: str,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    run_dir = store.runs / run.id
    events = run_dir / "events"
    displaced = store.tmp / "displaced-recovery-events"
    cached_before = (run_dir / "state.json").read_bytes()
    original_event_publish = store._publish_event
    original_cache_publish = store._replace_state_at
    replaced = False

    def replace_events() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        events.rename(displaced)
        events.mkdir(mode=0o700)

    def replace_before_event_publish(
        events_descriptor: int,
        revision: int,
        value: dict,
    ) -> None:
        replace_events()
        original_event_publish(events_descriptor, revision, value)

    def replace_at_cache_publish(
        run_descriptor: int,
        state,
        *,
        identity_guard=None,
    ) -> None:
        if replacement_boundary == "cache_prepublish":
            replace_events()
        original_cache_publish(
            run_descriptor,
            state,
            identity_guard=identity_guard,
        )
        if replacement_boundary == "cache_postpublish":
            replace_events()

    if replacement_boundary == "event_publish":
        monkeypatch.setattr(store, "_publish_event", replace_before_event_publish)
    else:
        monkeypatch.setattr(store, "_replace_state_at", replace_at_cache_publish)

    with pytest.raises(AnalysisError) as rejected:
        store.recover_interrupted()

    assert rejected.value.code == "analysis_storage_integrity"
    assert not list(events.iterdir())
    assert (run_dir / "state.json").read_bytes() == cached_before
    assert sorted(path.name for path in displaced.iterdir()) == [
        "00000000.json",
        "00000001.json",
    ]


def test_replaced_run_directory_fails_closed_without_cache_rewind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    reader = AnalysisStore.initialize(tmp_path)
    writer = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = writer.create_run(spec, source_id="src_" + ("a" * 32))
    writer.transition(run.id, "running")
    replayed = threading.Event()
    release_reader = threading.Event()
    original_names = reader._event_names

    def pause_after_replay(events_descriptor: int) -> tuple[str, ...]:
        names = original_names(events_descriptor)
        if threading.current_thread().name == "replaced-lock-reader":
            replayed.set()
            assert release_reader.wait(5)
        return names

    monkeypatch.setattr(reader, "_event_names", pause_after_replay)
    observed: dict[str, object] = {}

    def read_state() -> None:
        try:
            reader.load_state(run.id)
        except AnalysisError as error:
            observed["reader_error"] = error

    reader_thread = threading.Thread(target=read_state, name="replaced-lock-reader")
    reader_thread.start()
    assert replayed.wait(5)
    run_dir = writer.runs / run.id
    displaced = writer.tmp / "displaced-run"
    old_inode = run_dir.stat().st_ino
    run_dir.rename(displaced)
    shutil.copytree(displaced, run_dir)
    terminal = writer.transition(
        run.id,
        "failed",
        failure_code="fixture_failure",
        failure_message="Synthetic failure.",
        retryable=True,
    )
    assert run_dir.stat().st_ino != old_inode

    release_reader.set()
    reader_thread.join(5)

    assert not reader_thread.is_alive()
    assert observed["reader_error"].code == "analysis_storage_integrity"
    assert reader.load_state(run.id) == terminal
    cache = json.loads((writer.runs / run.id / "state.json").read_text(encoding="utf-8"))
    assert (cache["revision"], cache["status"]) == (2, "failed")


def test_missing_state_cache_remains_visible_and_recovers(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    cache = store.runs / run.id / "state.json"
    cache.unlink()

    listed = store.list_runs()

    assert [item["run_id"] for item in listed] == [run.id]
    assert listed[0]["status"] == "queued"
    assert cache.exists()
    cache.unlink()

    store.recover_interrupted()

    assert store.load_state(run.id).status == "failed"
    assert cache.exists()


def test_same_revision_cache_divergence_repairs_from_journal(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    expected = store.transition(run.id, "running")
    cache = store.runs / run.id / "state.json"
    divergent = expected.to_record_mapping()
    divergent.update(
        {
            "status": "failed",
            "failure_code": "fixture_failure",
            "failure_message": "Synthetic cache divergence.",
            "retryable": True,
        }
    )
    overwrite_private_json(cache, divergent)

    assert store.load_state(run.id) == expected
    assert json.loads(cache.read_text(encoding="utf-8")) == expected.to_record_mapping()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"failure_code": "impossible"}),
        lambda value: value.update({"failure_message": "impossible"}),
        lambda value: value.update({"retryable": True}),
    ],
)
def test_nonfailed_state_rejects_failure_fields(mutation) -> None:
    value = {
        "run_state_schema_version": "1.0.0-draft",
        "run_id": "run_" + ("a" * 32),
        "revision": 0,
        "status": "queued",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "failure_code": None,
        "failure_message": None,
        "retryable": False,
        "timeline_id": None,
    }
    mutation(value)

    with pytest.raises(ValueError):
        state_from_mapping(value)


def test_succeeded_state_requires_valid_published_output(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=spec.config.engine,
    )
    store.publish_success(run.id, timeline)
    output = store.runs / run.id / "output.json"
    overwrite_private_json(output, {"timeline_id": "sha256:" + ("b" * 64)})

    with pytest.raises(AnalysisError) as rejected:
        store.load_state(run.id)

    assert rejected.value.code == "analysis_storage_integrity"


def test_succeeded_transition_requires_publication_before_event(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")

    with pytest.raises(AnalysisError) as rejected:
        store.transition(
            run.id,
            "succeeded",
            timeline_id="sha256:" + ("b" * 64),
        )

    assert rejected.value.code == "analysis_storage_integrity"
    assert store.load_state(run.id).status == "running"
    assert not (store.runs / run.id / "events" / "00000002.json").exists()

    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="no_candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(),
        engine=spec.config.engine,
    )
    timeline_path = store._digest_path(store.timelines, timeline.id, create=True)
    store._publish_immutable(timeline_path, timeline.to_record_mapping())
    store._publish_immutable(
        store.runs / run.id / "output.json",
        {"timeline_id": timeline.id},
    )
    overwrite_private_json(timeline_path, {})

    with pytest.raises(AnalysisError) as malformed:
        store.transition(run.id, "succeeded", timeline_id=timeline.id)

    assert malformed.value.code == "analysis_storage_integrity"
    assert not (store.runs / run.id / "events" / "00000002.json").exists()


def test_publish_rejects_mismatched_engine_before_output(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    timebase = Timebase(8_000, 8_000)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    store.transition(run.id, "running")
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="candidates",
        beats=(),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            ChordSegment(
                0,
                spec.analyzed_range,
                "chord",
                (ChordCandidate("C:maj", "C:maj", 1, 800_000),),
                0,
            ),
        ),
        engine=EngineRef(engine_version="9.9.9"),
    )

    with pytest.raises(AnalysisError) as invalid:
        store.publish_success(run.id, timeline)

    assert invalid.value.code == "invalid_engine_output"
    assert not (store.runs / run.id / "output.json").exists()


def test_store_claim_is_cross_instance_exclusive(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    first = AnalysisStore.initialize(tmp_path)
    second = AnalysisStore.initialize(tmp_path)
    run_id = "run_" + ("a" * 32)
    other_run_id = "run_" + ("b" * 32)

    first.claim(run_id)
    with pytest.raises(AnalysisError) as claimed:
        second.claim(other_run_id)
    first.release_claim(run_id)

    assert claimed.value.code == "analysis_claimed"


def test_claim_spans_run_publication_against_concurrent_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProjectMediaStore.initialize(tmp_path)
    first = AnalysisStore.initialize(tmp_path)
    second = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    original = first._persist_run
    recovery_attempted = False

    def interleaved_persist(selected: AnalysisSpec, run) -> None:
        nonlocal recovery_attempted
        recovery_attempted = True
        second.recover_interrupted()
        original(selected, run)

    monkeypatch.setattr(first, "_persist_run", interleaved_persist)
    run = first.create_claimed_run(spec, source_id="src_" + ("a" * 32))
    try:
        assert recovery_attempted is True
        assert first.load_state(run.id).status == "queued"
    finally:
        first.release_claim(run.id)


@pytest.mark.parametrize("operation", ["recover", "claim"])
def test_claim_symlink_never_truncates_target(tmp_path: Path, operation: str) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TRUNCATE", encoding="utf-8")
    os.chmod(victim, 0o600)
    (store.claims / "active").symlink_to(victim)

    with pytest.raises(AnalysisError) as integrity:
        if operation == "recover":
            store.recover_interrupted()
        else:
            store.claim("run_" + ("a" * 32))

    assert integrity.value.code == "analysis_storage_integrity"
    assert victim.read_text(encoding="utf-8") == "DO NOT TRUNCATE"


def test_recovery_terminalizes_interrupted_run_and_allows_retry(tmp_path: Path) -> None:
    payload_path = tmp_path / "source.wav"
    write_progression(payload_path)
    media = ProjectMediaStore.initialize(tmp_path)
    with payload_path.open("rb") as handle:
        source, asset = media.import_stream(
            handle,
            byte_length=payload_path.stat().st_size,
            display_name="progression.wav",
            authorization_confirmed=True,
        )
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id=asset.id,
        timebase=asset.timebase,
        analyzed_range=FrameRange(0, asset.timebase.duration_frames),
    )
    run = store.create_run(spec, source_id=source.id)
    store.claim(run.id)
    store.transition(run.id, "running")
    descriptor = store._claim_handles.pop(run.id)
    os.close(descriptor)

    service = AnalysisService(tmp_path, wall_timeout_seconds=20)
    try:
        interrupted = service.status(run.id)
        assert interrupted["status"] == "failed"
        assert interrupted["failure_code"] == "analysis_interrupted"
        assert interrupted["retryable"] is True
        retry_id = service.retry(run.id)
        assert service.status(retry_id)["retry_of"] == run.id
        assert wait_terminal(service, retry_id)["status"] == "succeeded"
    finally:
        service.close()


def test_malformed_run_source_never_reaches_public_status(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )
    run = store.create_run(spec, source_id="src_" + ("a" * 32))
    request_path = store.runs / run.id / "request.json"
    value = json.loads(request_path.read_text(encoding="utf-8"))
    private_path = "/private/secret-session.wav"
    value["source_id"] = private_path
    request_path.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(request_path, 0o600)

    with pytest.raises(AnalysisError) as integrity:
        store.public_status(run.id)

    assert integrity.value.code == "analysis_storage_integrity"
    assert private_path not in integrity.value.public_message


def test_service_isolated_worker_success_idempotency_and_privacy(tmp_path: Path) -> None:
    payload_path = tmp_path / "source.wav"
    timebase, _reference = write_progression(payload_path)
    media = ProjectMediaStore.initialize(tmp_path)
    with payload_path.open("rb") as handle:
        source, asset = media.import_stream(
            handle,
            byte_length=payload_path.stat().st_size,
            display_name="/private/recordings/progression.wav",
            authorization_confirmed=True,
        )
    service = AnalysisService(tmp_path, wall_timeout_seconds=20)
    try:
        kwargs = {
            "source_id": source.id,
            "idempotency_key": "fixture-request-0001",
        }
        run_id = service.start(**kwargs)
        assert service.start(**kwargs) == run_id
        with pytest.raises(AnalysisError) as conflict:
            service.start(
                source_id=source.id,
                frame_range=FrameRange(0, timebase.duration_frames // 2),
                idempotency_key=kwargs["idempotency_key"],
            )
        assert conflict.value.code == "idempotency_conflict"
        with pytest.raises(AnalysisError) as busy:
            service.start(source_id=source.id, idempotency_key="fixture-request-0002")
        assert busy.value.code == "analysis_busy"
        status = wait_terminal(service, run_id)

        assert status["status"] == "succeeded"
        public = json.dumps({"status": status, "timeline": service.timeline(run_id)})
        assert str(tmp_path) not in public
        assert asset.sha256 not in public
        assert "spec_id" not in public
        assert "timeline_id" not in public
        assert "stack" not in public
    finally:
        service.close()


def test_supervisor_start_failure_releases_claim_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload_path = tmp_path / "source.wav"
    write_progression(payload_path)
    media = ProjectMediaStore.initialize(tmp_path)
    with payload_path.open("rb") as handle:
        source, _asset = media.import_stream(
            handle,
            byte_length=payload_path.stat().st_size,
            display_name="progression.wav",
            authorization_confirmed=True,
        )
    service = AnalysisService(tmp_path, wall_timeout_seconds=20)
    original_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda _self: (_ for _ in ()).throw(RuntimeError()))
    try:
        with pytest.raises(AnalysisError) as failed:
            service.start(source_id=source.id)
        assert failed.value.code == "supervisor_start_failed"
        failed_run = service.list(source_id=source.id)[0]
        assert failed_run["status"] == "failed"
        assert failed_run["retryable"] is True

        monkeypatch.setattr(threading.Thread, "start", original_start)
        retry_id = service.retry(failed_run["run_id"])
        assert wait_terminal(service, retry_id)["status"] == "succeeded"
    finally:
        service.close()


def test_service_cancel_is_terminal_and_retry_preserves_run(tmp_path: Path) -> None:
    payload_path = tmp_path / "source.wav"
    timebase, _reference = write_progression(payload_path, seconds_per_chord=8)
    media = ProjectMediaStore.initialize(tmp_path)
    with payload_path.open("rb") as handle:
        source, asset = media.import_stream(
            handle,
            byte_length=payload_path.stat().st_size,
            display_name="progression.wav",
            authorization_confirmed=True,
        )
    service = AnalysisService(tmp_path, wall_timeout_seconds=20)
    try:
        first = service.start(
            source_id=source.id,
        )
        service.cancel(first)
        first_status = wait_terminal(service, first)
        assert first_status["status"] == "cancelled"
        assert not (service.store.runs / first / "output.json").exists()
        second = service.retry(first)
        assert second != first
        assert service.status(second)["retry_of"] == first
        assert wait_terminal(service, second)["status"] == "succeeded"
        assert service.status(first)["status"] == "cancelled"
    finally:
        service.close()


def test_fixture_manifest_is_authorized_and_synthetic() -> None:
    path = Path(__file__).parent / "fixtures" / "analysis" / "fixture-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert all(item["generated"] and item["license"] == "MIT" for item in manifest["fixtures"])
    assert manifest["review"]["contains_third_party_recording"] is False
    assert manifest["review"]["redistribution_authorized"] is True


def test_analysis_storage_permissions_are_private(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)

    assert stat_mode(store.root) == 0o700
    assert all(stat_mode(path) == 0o700 for path in store.root.rglob("*") if path.is_dir())


def test_analysis_publication_rejects_intermediate_project_ancestor_replacement(
    tmp_path: Path,
) -> None:
    container = tmp_path / "selected"
    project = container / "project"
    project.mkdir(parents=True)
    ProjectMediaStore.initialize(project)
    store = AnalysisStore.initialize(project)
    container.rename(tmp_path / "selected-original")
    project.mkdir(parents=True)
    replacement = project / ".chordatlas"
    replacement.mkdir(mode=0o700)
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("a" * 64),
        timebase=Timebase(8_000, 8_000),
        analyzed_range=FrameRange(0, 8_000),
    )

    with pytest.raises(AnalysisError) as rejected:
        store.publish_spec(spec)

    assert rejected.value.code == "analysis_storage_integrity"
    assert list(replacement.iterdir()) == []


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
