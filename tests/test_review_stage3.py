from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import chordatlas.review.models as review_models_module
import chordatlas.review.store as review_store_module

from chordatlas.analysis import (
    AnalysisSpec,
    AnalysisStore,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    EngineRef,
)
from chordatlas.media import FrameRange, ProjectMediaStore, Timebase
from chordatlas.review import (
    ReviewEdit,
    ReviewError,
    ReviewService,
    ReviewStore,
    apply_edit,
    root_timeline,
)


def candidate_timeline() -> ChordCandidateTimeline:
    timebase = Timebase(1_000, 3_000)

    def candidate(label: str, rank: int, confidence: int) -> ChordCandidate:
        return ChordCandidate(label, label, rank, confidence)

    return ChordCandidateTimeline.create(
        spec_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=FrameRange(0, 3_000),
        result_kind="candidates",
        beats=(0, 500, 1_000, 1_500, 2_000, 2_500),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            ChordSegment(
                0,
                FrameRange(0, 1_000),
                "chord",
                (
                    candidate("C:maj", 1, 700_000),
                    candidate("A:min", 2, 200_000),
                ),
                0,
            ),
            ChordSegment(
                1,
                FrameRange(1_000, 2_000),
                "chord",
                (
                    candidate("G:maj", 1, 650_000),
                    candidate("C:maj", 2, 250_000),
                ),
                0,
            ),
            ChordSegment(
                2,
                FrameRange(2_000, 3_000),
                "chord",
                (candidate("A:min", 1, 720_000),),
                0,
            ),
        ),
        engine=EngineRef(),
    )


def provision_success(tmp_path: Path) -> tuple[str, ChordCandidateTimeline]:
    ProjectMediaStore.initialize(tmp_path)
    store = AnalysisStore.initialize(tmp_path)
    base = candidate_timeline()
    spec = AnalysisSpec.create(
        asset_id="sha256:" + ("b" * 64),
        timebase=base.timebase,
        analyzed_range=base.analyzed_range,
    )
    base = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=base.timebase,
        analyzed_range=base.analyzed_range,
        result_kind=base.result_kind,
        beats=base.beats,
        tempo_hypotheses=base.tempo_hypotheses,
        key_hypotheses=base.key_hypotheses,
        segments=base.segments,
        engine=base.engine,
    )
    run = store.create_run(spec, source_id="src_" + ("c" * 32))
    store.transition(run.id, "running")
    store.publish_success(run.id, base)
    return run.id, base


def edit(current, base, kind: str, parameters: dict):
    return apply_edit(
        current,
        ReviewEdit.create(kind, parameters),
        base=base,
        raw_root=root_timeline(base),
    )


def assert_geometry(timeline) -> None:
    assert timeline.segments[0].frame_range.start_frame == timeline.analyzed_range.start_frame
    assert timeline.segments[-1].frame_range.end_frame == timeline.analyzed_range.end_frame
    assert all(
        left.frame_range.end_frame == right.frame_range.start_frame
        for left, right in zip(timeline.segments, timeline.segments[1:])
    )
    assert all(item.frame_range.length_frames > 0 for item in timeline.segments)


def test_root_projection_is_deterministic_and_preserves_raw_candidates() -> None:
    base = candidate_timeline()
    before = json.dumps(base.to_record_mapping(), sort_keys=True)
    first = root_timeline(base)
    second = root_timeline(base)

    assert first == second
    assert first.phase == "unreviewed"
    assert first.summary_mapping()["unreviewed_segments"] == 3
    assert json.dumps(base.to_record_mapping(), sort_keys=True) == before
    assert first.to_public_mapping().get("base_timeline_id") is None


def test_review_fixture_is_authorized_redistributable_and_frame_exact() -> None:
    path = Path(__file__).parent / "fixtures" / "review" / "fixture-manifest.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))

    assert fixture["authorization"]["license"] == "CC0-1.0"
    assert fixture["authorization"]["redistributable"] is True
    assert fixture["timebase"]["unit"] == "sample_frame"
    assert fixture["segments"][0]["range"]["start_frame"] == 0
    assert fixture["segments"][-1]["range"]["end_frame"] == 3_000
    assert "lyrics" not in json.dumps(fixture).lower()


def test_alternate_manual_label_unknown_and_no_chord_are_distinct() -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    first_id = current.segments[0].id
    current = edit(
        current,
        base,
        "select_candidate",
        {"segment_id": first_id, "candidate_rank": 2},
    )
    assert current.segments[0].label == "A:min"
    assert current.segments[0].decision_origin == "candidate_selected"
    assert current.segments[0].label_status == "reviewed"

    current = edit(
        current,
        base,
        "set_label",
        {"segment_id": current.segments[0].id, "label": "C mystery"},
    )
    assert current.segments[0].label == "C mystery"
    assert current.segments[0].label_status == "unresolved"

    current = edit(
        current,
        base,
        "set_unknown",
        {"segment_id": current.segments[0].id},
    )
    assert current.segments[0].state == "unknown"
    assert current.segments[0].label_status == "unresolved"
    current = edit(
        current,
        base,
        "set_no_chord",
        {"segment_id": current.segments[0].id},
    )
    assert current.segments[0].state == "no_chord"
    assert current.segments[0].label_status == "reviewed"


@pytest.mark.parametrize(
    "label",
    ("C:maj", "A:min", "F#min7", "Bbmaj7", "Dsus4", "G7/B"),
)
def test_manual_label_accepts_stage2_and_supported_guitar_spellings(label: str) -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    current = edit(
        current,
        base,
        "set_label",
        {"segment_id": current.segments[0].id, "label": label},
    )
    assert current.segments[0].label == label
    assert current.segments[0].label_status == "reviewed"


@pytest.mark.parametrize("label", ("N.C.", "Unknown", " unknown "))
def test_manual_label_rejects_reserved_review_states(label: str) -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    with pytest.raises(ReviewError) as reserved:
        edit(
            current,
            base,
            "set_label",
            {"segment_id": current.segments[0].id, "label": label},
        )
    assert reserved.value.code == "reserved_chord_label"


@pytest.mark.parametrize("frame", [0, 1_000])
def test_split_rejects_endpoints_without_changing_state(frame: int) -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    before = current.id
    with pytest.raises(ReviewError) as invalid:
        edit(
            current,
            base,
            "split",
            {"segment_id": current.segments[0].id, "frame": frame},
        )
    assert invalid.value.code == "invalid_split"
    assert current.id == before


def test_split_merge_boundary_and_segment_move_preserve_geometry() -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    current = edit(
        current,
        base,
        "split",
        {"segment_id": current.segments[0].id, "frame": 400},
    )
    assert_geometry(current)
    current = edit(
        current,
        base,
        "merge",
        {
            "left_segment_id": current.segments[0].id,
            "right_segment_id": current.segments[1].id,
        },
    )
    assert_geometry(current)
    current = edit(
        current,
        base,
        "move_boundary",
        {
            "left_segment_id": current.segments[0].id,
            "right_segment_id": current.segments[1].id,
            "frame": 900,
        },
    )
    assert current.segments[0].frame_range.end_frame == 900
    duration = current.segments[1].frame_range.length_frames
    current = edit(
        current,
        base,
        "move_segment",
        {
            "segment_id": current.segments[1].id,
            "start_frame": 800,
            "resolution": "adjust_adjacent",
        },
    )
    assert current.segments[1].frame_range.length_frames == duration
    assert_geometry(current)


def test_move_rejects_edge_and_neighbor_consuming_ranges() -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    with pytest.raises(ReviewError) as edge:
        edit(
            current,
            base,
            "move_segment",
            {
                "segment_id": current.segments[0].id,
                "start_frame": 100,
                "resolution": "adjust_adjacent",
            },
        )
    assert edge.value.code == "move_edge_segment"
    with pytest.raises(ReviewError) as collision:
        edit(
            current,
            base,
            "move_segment",
            {
                "segment_id": current.segments[1].id,
                "start_frame": 0,
                "resolution": "adjust_adjacent",
            },
        )
    assert collision.value.code == "move_collision"


def test_atomic_no_chord_range_and_sections_preserve_coverage() -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    current = edit(
        current,
        base,
        "insert_no_chord",
        {"start_frame": 750, "end_frame": 1_250},
    )
    assert_geometry(current)
    no_chord = [item for item in current.segments if item.state == "no_chord"]
    assert [(item.frame_range.start_frame, item.frame_range.end_frame) for item in no_chord] == [
        (750, 1_250)
    ]
    current = edit(
        current,
        base,
        "add_section",
        {"frame": 0, "label": "Verse"},
    )
    marker = current.section_markers[0]
    current = edit(
        current,
        base,
        "rename_section",
        {"marker_id": marker.id, "label": "Verse A"},
    )
    current = edit(
        current,
        base,
        "move_section",
        {"marker_id": marker.id, "frame": 100},
    )
    assert current.section_markers[0].frame == 100
    current = edit(
        current,
        base,
        "remove_section",
        {"marker_id": marker.id},
    )
    assert not current.section_markers


def test_strict_integer_and_control_label_inputs() -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    for value in (True, 100.5, "100"):
        with pytest.raises(ReviewError):
            ReviewEdit.create(
                "split",
                {"segment_id": current.segments[0].id, "frame": value},
            )
    with pytest.raises(ReviewError):
        ReviewEdit.create(
            "set_label",
            {"segment_id": current.segments[0].id, "label": "C\n<script>"},
        )


def test_service_persists_undo_redo_branch_reset_and_ready(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-0001")
    session_id = created["session"]["session_id"]
    first_segment = created["timeline"]["segments"][0]["id"]
    changed = service.apply(
        session_id,
        expected_token=created["head"]["token"],
        idempotency_key="edit-review-0001",
        kind="set_label",
        parameters={"segment_id": first_segment, "label": "Cmaj7"},
    )
    undone = service.undo(
        session_id,
        expected_token=changed["head"]["token"],
        idempotency_key="undo-review-0001",
    )
    assert undone["timeline"]["segments"][0]["label"] == "C:maj"
    reopened = ReviewService(tmp_path).get(session_id)
    assert reopened["head"]["token"] == undone["head"]["token"]
    redone = service.redo(
        session_id,
        expected_token=undone["head"]["token"],
        idempotency_key="redo-review-0001",
        requested_revision_id=undone["head"]["redo_revision_id"],
    )
    assert redone["timeline"]["segments"][0]["label"] == "Cmaj7"
    reset = service.apply(
        session_id,
        expected_token=redone["head"]["token"],
        idempotency_key="reset-review-0001",
        kind="reset_to_raw",
        parameters={},
    )
    assert reset["timeline"]["segments"][0]["label"] == "C:maj"
    assert reset["local_measurements"]["reset_events"] == 1
    reviewed = reset
    for index, segment in enumerate(reset["timeline"]["segments"]):
        reviewed = service.apply(
            session_id,
            expected_token=reviewed["head"]["token"],
            idempotency_key=f"accept-review-ready-{index}",
            kind="accept_current",
            parameters={"segment_id": segment["id"]},
        )
    reviewed = service.apply(
        session_id,
        expected_token=reviewed["head"]["token"],
        idempotency_key="accept-review-boundaries",
        kind="accept_boundaries",
        parameters={},
    )
    ready = service.apply(
        session_id,
        expected_token=reviewed["head"]["token"],
        idempotency_key="ready-review-0001",
        kind="finish_review",
        parameters={},
    )
    assert ready["timeline"]["phase"] == "ready_for_approval"
    assert "song_chart" not in json.dumps(ready).lower()
    with pytest.raises(ReviewError) as closed:
        service.apply(
            session_id,
            expected_token=ready["head"]["token"],
            idempotency_key="edit-review-after-ready",
            kind="set_unknown",
            parameters={"segment_id": ready["timeline"]["segments"][0]["id"]},
        )
    assert closed.value.code == "review_ready"


def test_finish_requires_explicit_review_of_every_machine_proposal(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-finish")

    with pytest.raises(ReviewError) as incomplete:
        service.apply(
            created["session"]["session_id"],
            expected_token=created["head"]["token"],
            idempotency_key="finish-review-incomplete",
            kind="finish_review",
            parameters={},
        )

    assert incomplete.value.code == "review_unreviewed"
    reviewed = created
    for index, segment in enumerate(created["timeline"]["segments"]):
        reviewed = service.apply(
            created["session"]["session_id"],
            expected_token=reviewed["head"]["token"],
            idempotency_key=f"finish-review-label-{index}",
            kind="accept_current",
            parameters={"segment_id": segment["id"]},
        )
    with pytest.raises(ReviewError) as boundaries:
        service.apply(
            created["session"]["session_id"],
            expected_token=reviewed["head"]["token"],
            idempotency_key="finish-review-boundaries",
            kind="finish_review",
            parameters={},
        )
    assert boundaries.value.code == "review_boundaries_unreviewed"


def test_local_measurements_count_real_operations_and_wall_time_without_quality_claim(
    tmp_path: Path,
) -> None:
    run_id, _base = provision_success(tmp_path)
    instants: Iterator[str] = iter(
        (
            "2026-07-23T12:00:00+00:00",
            "2026-07-23T12:00:45+00:00",
        )
    )
    service = ReviewService(tmp_path, clock=lambda: next(instants))
    created = service.create(run_id, idempotency_key="create-review-evaluation")
    result = service.apply(
        created["session"]["session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="edit-review-evaluation",
        kind="select_candidate",
        parameters={
            "segment_id": created["timeline"]["segments"][0]["id"],
            "candidate_rank": 2,
        },
    )

    measurements = result["local_measurements"]
    assert measurements["accepted_edit_events"] == 1
    assert measurements["session_wall_elapsed_ms"] == 45_000
    assert measurements["wall_time_includes_idle"] is True
    assert measurements["external_telemetry"] is False
    assert "not accuracy, effort, productivity, or time saved" in measurements[
        "interpretation"
    ]


def test_backward_wall_clock_is_clamped_before_event_commit(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    instants = iter(
        (
            "2026-07-23T12:00:00+00:00",
            "2026-07-23T11:59:00+00:00",
        )
    )
    service = ReviewService(tmp_path, clock=lambda: next(instants))
    created = service.create(run_id, idempotency_key="create-review-clock")
    changed = service.apply(
        created["session"]["session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="edit-review-clock",
        kind="accept_current",
        parameters={"segment_id": created["timeline"]["segments"][0]["id"]},
    )

    assert changed["head"]["updated_at"] == created["session"]["created_at"]
    assert ReviewService(tmp_path).get(created["session"]["session_id"])[
        "head"
    ]["token"] == changed["head"]["token"]


def test_idempotency_and_monotonic_token_prevent_retry_double_count_and_aba(
    tmp_path: Path,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-0002")
    session_id = created["session"]["session_id"]
    segment_id = created["timeline"]["segments"][0]["id"]
    kwargs = {
        "session_id": session_id,
        "expected_token": created["head"]["token"],
        "idempotency_key": "edit-review-0002",
        "kind": "accept_current",
        "parameters": {"segment_id": segment_id},
    }
    first = service.apply(**kwargs)
    repeated = ReviewService(tmp_path).apply(**kwargs)
    assert repeated["head"]["token"] == first["head"]["token"]
    assert repeated["local_measurements"]["accepted_edit_events"] == 1
    undone = service.undo(
        session_id,
        expected_token=first["head"]["token"],
        idempotency_key="undo-review-0002",
    )
    assert undone["head"]["revision_id"] == created["head"]["revision_id"]
    assert undone["head"]["token"] != created["head"]["token"]
    with pytest.raises(ReviewError) as stale:
        service.apply(
            session_id,
            expected_token=created["head"]["token"],
            idempotency_key="edit-review-stale",
            kind="accept_current",
            parameters={"segment_id": segment_id},
        )
    assert stale.value.code == "review_precondition_failed"


@pytest.mark.parametrize("failed_publication", range(1, 8))
def test_session_creation_resumes_after_each_immutable_publication_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_publication: int,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    original = service.store._publish_immutable
    calls = 0

    def fail_at_boundary(path: Path, value: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_publication:
            raise OSError("synthetic create publication failure")
        original(path, value)

    monkeypatch.setattr(service.store, "_publish_immutable", fail_at_boundary)
    with pytest.raises(OSError):
        service.create(run_id, idempotency_key="create-review-resume")
    monkeypatch.setattr(service.store, "_publish_immutable", original)

    recovered = ReviewService(tmp_path).create(
        run_id,
        idempotency_key="create-review-resume",
    )
    assert recovered["timeline"]["phase"] == "unreviewed"
    assert len(ReviewService(tmp_path).list(analysis_run_id=run_id)) == 1


def test_session_creation_resumes_after_head_cache_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    original = service.store._replace_head

    def fail_head(_path: Path, _head) -> None:
        raise OSError("synthetic create head failure")

    monkeypatch.setattr(service.store, "_replace_head", fail_head)
    with pytest.raises(OSError):
        service.create(run_id, idempotency_key="create-review-head-resume")
    monkeypatch.setattr(service.store, "_replace_head", original)
    recovered = ReviewService(tmp_path).create(
        run_id,
        idempotency_key="create-review-head-resume",
    )
    assert recovered["head"]["generation"] == 0


def test_create_idempotency_key_conflicts_across_different_runs(tmp_path: Path) -> None:
    first_run, _base = provision_success(tmp_path)
    second_run, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    service.create(first_run, idempotency_key="create-review-project-key")

    with pytest.raises(ReviewError) as conflict:
        service.create(second_run, idempotency_key="create-review-project-key")

    assert conflict.value.code == "idempotency_conflict"


def test_exact_create_retry_returns_same_session_without_duplication(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    first = ReviewService(tmp_path).create(
        run_id,
        idempotency_key="create-review-exact-retry",
    )
    repeated = ReviewService(tmp_path).create(
        run_id,
        idempotency_key="create-review-exact-retry",
    )

    assert repeated["session"] == first["session"]
    assert repeated["head"]["token"] == first["head"]["token"]
    assert len(ReviewService(tmp_path).list(analysis_run_id=run_id)) == 1


def test_edit_retry_after_pre_event_crash_reuses_identical_revision_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _base = provision_success(tmp_path)
    instants = iter(
        (
            "2026-07-23T12:00:00+00:00",
            "2026-07-23T12:00:01+00:00",
            "2026-07-23T12:00:02+00:00",
        )
    )
    service = ReviewService(tmp_path, clock=lambda: next(instants))
    created = service.create(run_id, idempotency_key="create-review-revision-crash")
    original = service.store._commit_event

    def fail_event(*_args, **_kwargs) -> None:
        raise OSError("synthetic pre-event crash")

    monkeypatch.setattr(service.store, "_commit_event", fail_event)
    kwargs = {
        "session_id": created["session"]["session_id"],
        "expected_token": created["head"]["token"],
        "idempotency_key": "edit-review-revision-crash",
        "kind": "accept_current",
        "parameters": {"segment_id": created["timeline"]["segments"][0]["id"]},
    }
    with pytest.raises(OSError):
        service.apply(**kwargs)
    monkeypatch.setattr(service.store, "_commit_event", original)
    recovered = service.apply(**kwargs)
    assert recovered["local_measurements"]["accepted_edit_events"] == 1


def test_revision_limit_rejects_before_committing_an_unreadable_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review_store_module, "MAX_REVISIONS", 3)
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    current = service.create(run_id, idempotency_key="create-review-limit")
    session_id = current["session"]["session_id"]
    segment_id = current["timeline"]["segments"][0]["id"]
    for index in range(2):
        current = service.apply(
            session_id,
            expected_token=current["head"]["token"],
            idempotency_key=f"edit-review-limit-{index}",
            kind="accept_current",
            parameters={"segment_id": segment_id},
        )
    with pytest.raises(ReviewError) as limited:
        service.apply(
            session_id,
            expected_token=current["head"]["token"],
            idempotency_key="edit-review-limit-rejected",
            kind="accept_current",
            parameters={"segment_id": segment_id},
        )
    assert limited.value.code == "review_limit"
    assert ReviewService(tmp_path).get(session_id)["head"]["token"] == current["head"]["token"]


def test_edit_after_undo_preserves_old_revision_but_clears_redo_path(
    tmp_path: Path,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-branch")
    session_id = created["session"]["session_id"]
    segment_id = created["timeline"]["segments"][0]["id"]
    first = service.apply(
        session_id,
        expected_token=created["head"]["token"],
        idempotency_key="edit-review-branch-old",
        kind="set_label",
        parameters={"segment_id": segment_id, "label": "C7"},
    )
    old_revision = first["head"]["revision_id"]
    undone = service.undo(
        session_id,
        expected_token=first["head"]["token"],
        idempotency_key="undo-review-branch",
    )
    replacement = service.apply(
        session_id,
        expected_token=undone["head"]["token"],
        idempotency_key="edit-review-branch-new",
        kind="set_label",
        parameters={"segment_id": segment_id, "label": "C9"},
    )

    assert replacement["head"]["can_redo"] is False
    assert replacement["timeline"]["segments"][0]["label"] == "C9"
    assert service.store._load_revision(old_revision).id == old_revision


def test_head_event_repairs_cache_after_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-0003")
    session_id = created["session"]["session_id"]
    original = service.store._replace_head
    calls = 0

    def fail_once(path: Path, head) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic head-cache crash")
        original(path, head)

    monkeypatch.setattr(service.store, "_replace_head", fail_once)
    with pytest.raises(OSError):
        service.apply(
            session_id,
            expected_token=created["head"]["token"],
            idempotency_key="edit-review-0003",
            kind="accept_current",
            parameters={"segment_id": created["timeline"]["segments"][0]["id"]},
        )
    repaired = ReviewService(tmp_path).get(session_id)
    assert repaired["head"]["generation"] == 1
    assert repaired["timeline"]["segments"][0]["label_status"] == "reviewed"


def test_two_store_instances_allow_exactly_one_expected_head_mutation(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    first = ReviewService(tmp_path)
    created = first.create(run_id, idempotency_key="create-review-0004")
    second = ReviewService(tmp_path)
    outcomes: list[str] = []

    def mutate(service: ReviewService, key: str, label: str) -> None:
        try:
            service.apply(
                created["session"]["session_id"],
                expected_token=created["head"]["token"],
                idempotency_key=key,
                kind="set_label",
                parameters={
                    "segment_id": created["timeline"]["segments"][0]["id"],
                    "label": label,
                },
            )
            outcomes.append("won")
        except ReviewError as error:
            outcomes.append(error.code)

    threads = (
        threading.Thread(target=mutate, args=(first, "edit-review-race1", "C7")),
        threading.Thread(target=mutate, args=(second, "edit-review-race2", "C9")),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["review_precondition_failed", "won"]


def test_public_review_payload_excludes_private_identifiers_and_paths(tmp_path: Path) -> None:
    run_id, base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    value = service.create(run_id, idempotency_key="create-review-0005")
    public = json.dumps(value)

    assert str(tmp_path) not in public
    assert base.id not in public
    assert base.spec_id not in public
    assert "sha256:" not in public
    assert "idempotency" not in public
    assert "media bytes" not in public.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    (("generation", True), ("accepted_edit_events", 999)),
)
def test_malformed_or_impossible_head_event_fails_closed(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key=f"create-review-event-{field}")
    session_id = created["session"]["session_id"]
    event_path = service.store.sessions / session_id / "events" / "00000000.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["head"][field] = value
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(ReviewError) as integrity:
        ReviewService(tmp_path).get(session_id)

    assert integrity.value.code == "review_storage_integrity"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda text: text.replace(
            '  "review_head_event_schema_version": "1.0.0-draft"\n',
            '  "review_head_event_schema_version": "1.0.0-draft",\n'
            '  "review_head_event_schema_version": "1.0.0-draft"\n',
            1,
        ),
        lambda text: text.replace('"generation": 0', '"generation": NaN', 1),
    ),
)
def test_noncanonical_head_event_json_fails_closed(
    tmp_path: Path,
    mutation,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-json")
    session_id = created["session"]["session_id"]
    event = service.store.sessions / session_id / "events" / "00000000.json"
    original = event.read_text(encoding="utf-8")
    changed = mutation(original)
    assert changed != original
    event.write_text(changed, encoding="utf-8")
    os.chmod(event, 0o600)

    with pytest.raises(ReviewError) as integrity:
        ReviewService(tmp_path).get(session_id)

    assert integrity.value.code == "review_storage_integrity"


def test_private_timestamp_text_cannot_escape_through_session_listing(
    tmp_path: Path,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-timestamp")
    session_id = created["session"]["session_id"]
    session_path = service.store.sessions / session_id / "session.json"
    record = json.loads(session_path.read_text(encoding="utf-8"))
    record["created_at"] = "/private/secret-project.wav"
    session_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ReviewError) as integrity:
        ReviewService(tmp_path).list(analysis_run_id=run_id)

    assert integrity.value.code == "review_storage_integrity"


def test_malformed_create_receipt_timestamp_fails_closed(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    service.create(run_id, idempotency_key="create-review-receipt-time")
    receipt_path = next(service.store.create_keys.glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["created_at"] = "/private/bad"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ReviewError) as integrity:
        ReviewService(tmp_path).create(
            run_id,
            idempotency_key="create-review-receipt-time",
        )

    assert integrity.value.code == "review_storage_integrity"


def test_edit_cardinality_limits_fail_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = candidate_timeline()
    current = root_timeline(base)
    monkeypatch.setattr(review_models_module, "MAX_SEGMENTS", len(current.segments))
    with pytest.raises(ReviewError) as split_limit:
        edit(
            current,
            base,
            "split",
            {"segment_id": current.segments[0].id, "frame": 500},
        )
    assert split_limit.value.code == "review_limit"

    monkeypatch.setattr(review_models_module, "MAX_MARKERS", 0)
    with pytest.raises(ReviewError) as marker_limit:
        edit(current, base, "add_section", {"frame": 0, "label": "Verse"})
    assert marker_limit.value.code == "review_limit"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow")
def test_review_lock_symlink_never_truncates_target(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = ReviewStore.initialize(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TRUNCATE", encoding="utf-8")
    os.chmod(victim, 0o600)
    (store.locks / "project.lock").symlink_to(victim)

    with pytest.raises(ReviewError) as integrity:
        store.create_session(
            source_id="src_" + ("a" * 32),
            analysis_run_id="run_" + ("b" * 32),
            base=candidate_timeline(),
            idempotency_key="create-review-lock",
            created_at="2026-07-23T00:00:00+00:00",
        )

    assert integrity.value.code == "review_storage_integrity"
    assert victim.read_text(encoding="utf-8") == "DO NOT TRUNCATE"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow")
@pytest.mark.parametrize("target_kind", ("session", "events"))
def test_review_directory_symlink_traversal_is_rejected(
    tmp_path: Path,
    target_kind: str,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(
        run_id,
        idempotency_key=f"create-review-directory-{target_kind}",
    )
    session_id = created["session"]["session_id"]
    session_dir = service.store.sessions / session_id
    target = session_dir if target_kind == "session" else session_dir / "events"
    moved = tmp_path / f"moved-{target_kind}"
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=True)

    with pytest.raises(ReviewError) as integrity:
        ReviewService(tmp_path).get(session_id)

    assert integrity.value.code == "review_storage_integrity"


def test_hardlinked_immutable_review_record_is_rejected(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-hardlink")
    session = service.store.load_session(created["session"]["session_id"])
    root = service.store._load_revision(session.root_revision_id)
    timeline_path = service.store._digest_path(
        service.store.timelines,
        root.timeline_id,
        "rtl_",
    )
    alias = tmp_path / "unexpected-hardlink.json"
    os.link(timeline_path, alias)
    try:
        with pytest.raises(ReviewError) as integrity:
            service.get(session.id)
        assert integrity.value.code == "review_storage_integrity"
    finally:
        alias.unlink()


def test_stage2_timeline_file_is_byte_identical_after_review(tmp_path: Path) -> None:
    run_id, _base = provision_success(tmp_path)
    analysis = AnalysisStore.initialize(tmp_path)
    timeline_id = analysis.load_state(run_id).timeline_id
    assert timeline_id is not None
    timeline_path = analysis._digest_path(analysis.timelines, timeline_id)
    before = hashlib.sha256(timeline_path.read_bytes()).digest()
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-0006")
    service.apply(
        created["session"]["session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="edit-review-0006",
        kind="accept_current",
        parameters={"segment_id": created["timeline"]["segments"][0]["id"]},
    )
    assert hashlib.sha256(timeline_path.read_bytes()).digest() == before


def test_review_rejects_replaced_fixed_storage_ancestor(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    store = ReviewStore.initialize(tmp_path)
    original = tmp_path / ".chordatlas"
    displaced = tmp_path / "displaced-chordatlas"
    original.rename(displaced)
    replacement = tmp_path / ".chordatlas"
    replacement.mkdir(mode=0o700)

    with pytest.raises(ReviewError) as integrity:
        store.create_session(
            source_id="src_" + ("a" * 32),
            analysis_run_id="run_" + ("b" * 32),
            base=candidate_timeline(),
            idempotency_key="create-review-replaced-root",
            created_at="2026-07-23T00:00:00+00:00",
        )

    assert integrity.value.code == "review_storage_integrity"
    assert list(replacement.iterdir()) == []


def test_review_rejects_intermediate_project_ancestor_replacement(
    tmp_path: Path,
) -> None:
    container = tmp_path / "selected"
    project = container / "project"
    project.mkdir(parents=True)
    ProjectMediaStore.initialize(project)
    store = ReviewStore.initialize(project)
    container.rename(tmp_path / "selected-original")
    project.mkdir(parents=True)
    replacement = project / ".chordatlas"
    replacement.mkdir(mode=0o700)

    with pytest.raises(ReviewError) as rejected:
        store.create_session(
            source_id="src_" + ("a" * 32),
            analysis_run_id="run_" + ("b" * 32),
            base=candidate_timeline(),
            idempotency_key="create-review-replaced-project",
            created_at="2026-07-23T00:00:00+00:00",
        )

    assert rejected.value.code == "review_storage_integrity"
    assert list(replacement.iterdir()) == []


def test_review_record_read_is_bounded_during_concurrent_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _base = provision_success(tmp_path)
    service = ReviewService(tmp_path)
    created = service.create(run_id, idempotency_key="create-review-growth")
    session_id = created["session"]["session_id"]
    path = service.store.sessions / session_id / "session.json"
    identity = (path.stat().st_dev, path.stat().st_ino)
    original_read = review_store_module.os.read
    grew = False

    def grow_after_first_read(descriptor: int, count: int) -> bytes:
        nonlocal grew
        chunk = original_read(descriptor, count)
        info = os.fstat(descriptor)
        if not grew and (info.st_dev, info.st_ino) == identity:
            grew = True
            with path.open("ab") as handle:
                handle.write(b" " * (review_store_module._MAX_JSON_BYTES + 1))
                handle.flush()
                os.fsync(handle.fileno())
        return chunk

    monkeypatch.setattr(review_store_module.os, "read", grow_after_first_read)
    with pytest.raises(ReviewError) as rejected:
        service.get(session_id)

    assert grew is True
    assert rejected.value.code == "review_storage_integrity"
