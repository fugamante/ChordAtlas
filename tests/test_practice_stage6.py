from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import pytest

import chordatlas._fs as durable_fs
import chordatlas.practice.store as practice_store_module
from chordatlas.analysis import (
    AnalysisSpec,
    AnalysisStore,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    EngineRef,
)
from chordatlas.media import FrameRange, ProjectMediaStore, RemoteLocator, Timebase
from chordatlas.practice import (
    PracticeAttempt,
    PracticeError,
    PracticeHead,
    PracticePlaybackController,
    PracticeSession,
    PracticeService,
    PracticeStore,
    PracticeTarget,
)
from chordatlas.promotion import PromotionService
from chordatlas.review import ReviewService
from test_studio_stage1 import synthetic_wav


def approved_fixture(
    tmp_path: Path,
    *,
    remote: bool = False,
) -> tuple[PracticeService, PromotionService, ProjectMediaStore, str, str]:
    media = ProjectMediaStore.initialize(tmp_path)
    payload = synthetic_wav(frames=32_000, sample_rate=8_000)
    if remote:
        source_id = "src_" + ("d" * 32)
        stage = media.allocate_private_stage()
        stage.write_bytes(payload)
        os.chmod(stage, 0o600)
        source, asset = media.publish_staged_file(
            stage,
            byte_length=len(payload),
            display_name="authorized-synthetic.wav",
            authorization_confirmed=True,
            source_id=source_id,
            locator=RemoteLocator(
                source_id=source_id,
                channel="direct_https",
                private_locator="https://audio.example/authorized-synthetic",
            ),
        )
    else:
        source, asset = media.import_stream(
            io.BytesIO(payload),
            byte_length=len(payload),
            display_name="authorized-synthetic.wav",
            authorization_confirmed=True,
        )
    timebase = Timebase(8_000, 32_000)
    analysis = AnalysisStore.initialize(tmp_path)
    spec = AnalysisSpec.create(
        asset_id=asset.id,
        timebase=timebase,
        analyzed_range=FrameRange(0, 32_000),
    )
    run = analysis.create_run(spec, source_id=source.id)
    analysis.transition(run.id, "running")
    labels = ("C:maj", "G:maj", "A:min", "F:maj")
    segments = tuple(
        ChordSegment(
            ordinal,
            FrameRange(ordinal * 8_000, (ordinal + 1) * 8_000),
            "chord",
            (ChordCandidate(label, label, 1, 800_000),),
            0,
        )
        for ordinal, label in enumerate(labels)
    )
    timeline = ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=timebase,
        analyzed_range=spec.analyzed_range,
        result_kind="candidates",
        beats=tuple(range(0, 32_000, 2_000)),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=segments,
        engine=EngineRef(),
    )
    analysis.publish_success(run.id, timeline)

    review = ReviewService(tmp_path)
    value = review.create(run.id, idempotency_key="stage6-review-create")
    session_id = value["session"]["session_id"]
    for index, segment in enumerate(value["timeline"]["segments"]):
        value = review.apply(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key=f"stage6-accept-{index}",
            kind="accept_current",
            parameters={"segment_id": segment["id"]},
        )
    value = review.apply(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-boundaries",
        kind="accept_boundaries",
        parameters={},
    )
    for index, (frame, label) in enumerate(((16_000, "Chorus"), (24_000, "Verse"))):
        value = review.apply(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key=f"stage6-section-{index}",
            kind="add_section",
            parameters={"frame": frame, "label": label},
        )
    value = review.apply(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-finish",
        kind="finish_review",
        parameters={},
    )

    promotion = PromotionService(tmp_path)
    mapping = {
        "mapping_version": "songchart-explicit-grid-v1",
        "title": "Authorized synthetic practice chart",
        "artist": None,
        "key": None,
        "tuning": "Standard",
        "capo": "None",
        "chart_version": "1.0",
        "meter_numerator": 4,
        "beat_unit": 4,
        "measure_boundaries_frames": [0, 8_000, 16_000, 24_000, 32_000],
        "pickup_policy": "full_coverage_confirmed",
        "default_section_name": "Verse",
        "notation_mode": "sounding",
        "diagram_policy": "built_in_standard_only",
        "loss_policy": "allow_declared",
        "guitar_decisions": [
            {
                "chord": label,
                "inversion_reviewed": True,
                "voicing": "built_in",
                "playability": "playable",
                "note": None,
            }
            for label in ("Am", "C", "F", "G")
        ],
    }
    preview = promotion.preview(
        session_id,
        value["head"]["revision_id"],
        expected_head_token=value["head"]["token"],
        config_mapping=mapping,
    )
    material = tuple(
        sorted(
            item["id"]
            for item in preview["issues"]
            if item["severity"] == "material"
        )
    )
    approved = promotion.approve(
        session_id,
        value["head"]["revision_id"],
        expected_head_token=value["head"]["token"],
        idempotency_key="stage6-approval",
        config_mapping=mapping,
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=material,
    )
    approval_id = approved["approval"]["approval_id"]
    return PracticeService(tmp_path), promotion, media, source.id, approval_id


def test_practice_session_maps_full_repeated_sections_and_measures(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    session = value["practice_session"]
    assert session["timebase"] == {
        "unit": "sample_frame",
        "sample_rate": 8_000,
        "duration_frames": 32_000,
    }
    targets = session["targets"]
    assert [item["kind"] for item in targets] == [
        "full",
        "section",
        "section",
        "section",
        "measure",
        "measure",
        "measure",
        "measure",
    ]
    verse = [item for item in targets if item["kind"] == "section" and item["label"] == "Verse"]
    assert [(item["occurrence"], item["range"]) for item in verse] == [
        (1, {"start_frame": 0, "end_frame": 16_000}),
        (2, {"start_frame": 24_000, "end_frame": 32_000}),
    ]
    assert practice.list(source_id=source_id)[0]["practice_session"]["practice_session_id"] == (
        session["practice_session_id"]
    )


def test_attempt_update_is_immutable_idempotent_and_restores_paused(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    session_id = value["practice_session"]["practice_session_id"]
    chorus = next(
        item
        for item in value["practice_session"]["targets"]
        if item["kind"] == "section" and item["label"] == "Chorus"
    )
    payload = {
        "target_id": chorus["target_id"],
        "custom_range": None,
        "position_frame": 16_000,
        "loop_enabled": True,
        "rate_milli": 750,
        "count_in": "one_approved_bar",
    }
    first = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-save-chorus",
        state_mapping=payload,
    )
    repeated = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-save-chorus",
        state_mapping=payload,
    )
    assert repeated["attempt"]["attempt_id"] == first["attempt"]["attempt_id"]
    assert first["attempt"]["range"] == {"start_frame": 16_000, "end_frame": 24_000}
    assert first["attempt"]["rate_percent"] == 75.0
    assert first["attempt"]["count_in"] == {
        "mode": "one_approved_bar",
        "beats": 4,
        "beat_duration_ms": 333,
        "timing_authority": "approved_measure_grid",
    }
    assert first["attempt"]["restored_playing"] is False
    restored = PracticeService(tmp_path).get(session_id)
    assert restored["attempt"] == first["attempt"]
    assert restored["head"] == first["head"]
    assert first["attempt"]["attempt_id"] != value["attempt"]["attempt_id"]
    store = PracticeStore.initialize(tmp_path)
    assert store.load_attempt(value["attempt"]["attempt_id"]).parent_attempt_id is None


def test_custom_range_boundaries_rate_and_conflict_fail_closed(tmp_path: Path) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    session_id = value["practice_session"]["practice_session_id"]
    custom = {
        "target_id": None,
        "custom_range": {"start_frame": 8_000, "end_frame": 16_000},
        "position_frame": 8_000,
        "loop_enabled": True,
        "rate_milli": 1_250,
        "count_in": "off",
    }
    saved = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-save-custom",
        state_mapping=custom,
    )
    assert saved["attempt"]["selection_kind"] == "custom"
    assert saved["attempt"]["target_id"] is None
    for invalid in (
        {**custom, "custom_range": {"start_frame": 16_000, "end_frame": 16_000}},
        {**custom, "position_frame": 16_000},
        {**custom, "rate_milli": 1_251},
        {**custom, "rate_milli": True},
    ):
        with pytest.raises(PracticeError):
            practice.update(
                session_id,
                expected_token=saved["head"]["token"],
                idempotency_key=f"invalid-{json.dumps(invalid, sort_keys=True)}",
                state_mapping=invalid,
            )
    with pytest.raises(PracticeError) as conflict:
        practice.update(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key="stage6-stale-update",
            state_mapping=custom,
        )
    assert conflict.value.code == "practice_conflict"


def test_approval_revocation_blocks_changes_but_preserves_private_history(
    tmp_path: Path,
) -> None:
    practice, promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    approval = promotion.get(approval_id)["approval"]
    promotion.revoke(
        approval_id,
        expected_token=approval["token"],
        idempotency_key="stage6-revoke",
        reason="superseded",
    )
    restored = practice.get(value["practice_session"]["practice_session_id"])
    assert restored["availability"]["status"] == "approval_revoked"
    with pytest.raises(PracticeError) as blocked:
        practice.update(
            value["practice_session"]["practice_session_id"],
            expected_token=value["head"]["token"],
            idempotency_key="stage6-after-revoke",
            state_mapping={
                "target_id": value["attempt"]["target_id"],
                "custom_range": None,
                "position_frame": 0,
                "loop_enabled": True,
                "rate_milli": 1_000,
                "count_in": "off",
            },
        )
    assert blocked.value.code == "approval_revoked"


def test_forgetting_remote_locator_preserves_practice_and_media(
    tmp_path: Path,
) -> None:
    practice, _promotion, media, source_id, approval_id = approved_fixture(
        tmp_path,
        remote=True,
    )
    value = practice.create(approval_id, idempotency_key="stage6-remote-practice")
    assert media.forget_remote_locator(source_id)
    restored = practice.get(value["practice_session"]["practice_session_id"])
    assert restored["availability"]["status"] == "ready"
    assert media.audio_path_for_source(source_id).is_file()


def test_missing_media_disables_practice_without_changing_history(
    tmp_path: Path,
) -> None:
    practice, _promotion, media, source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    media.audio_path_for_source(source_id).unlink()
    restored = practice.get(value["practice_session"]["practice_session_id"])
    assert restored["availability"]["status"] == "media_unavailable"
    assert restored["attempt"]["attempt_id"] == value["attempt"]["attempt_id"]


def test_practice_storage_rejects_symlink_and_hardlink_records(tmp_path: Path) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-practice-create")
    session_id = value["practice_session"]["practice_session_id"]
    store = PracticeStore.initialize(tmp_path)
    session_path = store.sessions / f"{session_id}.json"
    alias = tmp_path / "session-alias.json"
    os.link(session_path, alias)
    with pytest.raises(PracticeError) as linked:
        store.load_session(session_id)
    assert linked.value.code == "practice_storage_integrity"
    alias.unlink()
    original = session_path.read_bytes()
    session_path.unlink()
    target = tmp_path / "replacement.json"
    target.write_bytes(original)
    os.chmod(target, 0o600)
    session_path.symlink_to(target)
    with pytest.raises(PracticeError) as symlinked:
        store.load_session(session_id)
    assert symlinked.value.code == "practice_storage_integrity"


def test_runtime_count_in_loop_and_rate_keep_source_frame_authority() -> None:
    controller = PracticePlaybackController(
        Timebase(8_000, 32_000),
        loop=FrameRange(8_000, 16_000),
        rate_milli=750,
        count_in_beats=4,
    )
    assert not controller.start().playing
    for remaining in (3, 2, 1):
        assert controller.count_in_tick().count_in_remaining == remaining
        assert not controller.state.playing
    assert controller.count_in_tick().playing
    assert controller.advance_source_frames(8_500).position_frame == 8_500
    assert controller.state.completed_loops == 1
    assert controller.wall_milliseconds_for_source_frames(8_000) == 1_333
    assert controller.state.loop == FrameRange(8_000, 16_000)


def test_practice_never_enters_songchart_exports(tmp_path: Path) -> None:
    practice, promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-private-sentinel")
    exports = promotion.exports(approval_id)
    joined = json.dumps(exports)
    assert value["practice_session"]["practice_session_id"] not in joined
    assert value["attempt"]["attempt_id"] not in joined
    assert "practice_session" not in joined
    assert ".chordatlas" not in joined
    assert str(tmp_path) not in joined


def test_practice_records_round_trip_strictly_and_serialize_deterministically(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-roundtrip")
    store = PracticeStore.initialize(tmp_path)
    session, head, attempt = store.current(
        value["practice_session"]["practice_session_id"]
    )
    assert PracticeSession.from_mapping(session.to_record_mapping()) == session
    assert PracticeHead.from_mapping(head.to_record_mapping()) == head
    assert PracticeAttempt.from_mapping(attempt.to_record_mapping()) == attempt
    assert all(
        PracticeTarget.from_mapping(target.to_record_mapping()) == target
        for target in session.targets
    )
    encoded = json.dumps(
        session.to_record_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert encoded == json.dumps(
        session.to_record_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    invalid = attempt.to_record_mapping()
    invalid["rate_milli"] = True
    with pytest.raises(ValueError):
        PracticeAttempt.from_mapping(invalid)


def test_interrupted_head_commit_is_repaired_by_same_idempotent_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-recovery-create")
    session_id = value["practice_session"]["practice_session_id"]
    payload = {
        "target_id": value["attempt"]["target_id"],
        "custom_range": None,
        "position_frame": 0,
        "loop_enabled": True,
        "rate_milli": 900,
        "count_in": "off",
    }
    original = practice.store._replace_head
    monkeypatch.setattr(
        practice.store,
        "_replace_head",
        lambda _head: (_ for _ in ()).throw(
            PracticeError(
                "practice_storage_integrity",
                "Private practice storage failed an integrity check.",
            )
        ),
    )
    with pytest.raises(PracticeError):
        practice.update(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key="stage6-recovery-save",
            state_mapping=payload,
        )
    monkeypatch.setattr(practice.store, "_replace_head", original)
    repaired = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-recovery-save",
        state_mapping=payload,
    )
    assert repaired["head"]["generation"] == 1
    assert repaired["attempt"]["rate_milli"] == 900


def test_valid_old_head_is_reconciled_to_complete_receipt_lineage(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-rollback-create")
    session_id = value["practice_session"]["practice_session_id"]

    def state(rate_milli: int) -> dict:
        return {
            "target_id": value["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 0,
            "loop_enabled": True,
            "rate_milli": rate_milli,
            "count_in": "off",
        }

    first = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-rollback-first",
        state_mapping=state(900),
    )
    old_head = practice.store.load_head(session_id)
    second = practice.update(
        session_id,
        expected_token=first["head"]["token"],
        idempotency_key="stage6-rollback-second",
        state_mapping=state(800),
    )

    practice.store._replace_head(old_head)
    restored = PracticeService(tmp_path).get(session_id)
    assert restored["head"]["generation"] == second["head"]["generation"]
    assert restored["attempt"]["attempt_id"] == second["attempt"]["attempt_id"]

    with pytest.raises(PracticeError) as stale:
        practice.update(
            session_id,
            expected_token=old_head.token,
            idempotency_key="stage6-rollback-fresh-branch",
            state_mapping=state(750),
        )
    assert stale.value.code == "practice_conflict"


def test_ambiguous_complete_receipt_branches_fail_closed(tmp_path: Path) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-branch-create")
    session_id = value["practice_session"]["practice_session_id"]
    state = {
        "target_id": value["attempt"]["target_id"],
        "custom_range": None,
        "position_frame": 0,
        "loop_enabled": True,
        "rate_milli": 900,
        "count_in": "off",
    }
    first = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-branch-first",
        state_mapping=state,
    )
    state["rate_milli"] = 800
    practice.update(
        session_id,
        expected_token=first["head"]["token"],
        idempotency_key="stage6-branch-second",
        state_mapping=state,
    )

    parent = practice.store.load_attempt(first["attempt"]["attempt_id"])
    sibling = PracticeAttempt.create(
        session_id=session_id,
        parent_attempt_id=parent.id,
        selection_kind=parent.selection_kind,
        target_id=parent.target_id,
        frame_range=parent.frame_range,
        position_frame=parent.position_frame,
        loop_enabled=parent.loop_enabled,
        rate_milli=750,
        count_in_beats=parent.count_in_beats,
        count_in_beat_frames_num=parent.count_in_beat_frames_num,
        count_in_beat_frames_den=parent.count_in_beat_frames_den,
        recorded_at="2026-07-23T12:00:00+00:00",
    )
    practice.store._publish_attempt(sibling)
    key_hash = practice_store_module._idempotency_hash("stage6-branch-sibling")
    practice_store_module._publish_json(
        practice.store.receipts / f"{key_hash}.json",
        {
            "practice_receipt_schema_version": "1.0.0-draft",
            "key_hash": key_hash,
            "action": "update",
            "fingerprint": "f" * 64,
            "recorded_at": sibling.recorded_at,
            "attempt_id": sibling.id,
            "parent_attempt_id": parent.id,
            "generation": 2,
        },
    )

    with pytest.raises(PracticeError) as ambiguous:
        PracticeService(tmp_path).get(session_id)
    assert ambiguous.value.code == "practice_storage_integrity"


def test_concurrent_writers_require_exact_head_and_do_not_overwrite(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-concurrent-create")
    session_id = value["practice_session"]["practice_session_id"]
    outcomes: list[str] = []

    def write(rate: int) -> None:
        service = PracticeService(tmp_path)
        try:
            service.update(
                session_id,
                expected_token=value["head"]["token"],
                idempotency_key=f"stage6-concurrent-{rate}",
                state_mapping={
                    "target_id": value["attempt"]["target_id"],
                    "custom_range": None,
                    "position_frame": 0,
                    "loop_enabled": True,
                    "rate_milli": rate,
                    "count_in": "off",
                },
            )
            outcomes.append("saved")
        except PracticeError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=write, args=(rate,)) for rate in (750, 900)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["practice_conflict", "saved"]
    assert PracticeService(tmp_path).get(session_id)["head"]["generation"] == 1


def test_changed_review_marks_practice_stale_without_retargeting(
    tmp_path: Path,
) -> None:
    practice, promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-stale-create")
    basis = promotion.load_practice_basis(approval_id, require_active=True)
    review = ReviewService(tmp_path)
    current = review.get(basis.review_session_id)
    review.undo(
        basis.review_session_id,
        expected_token=current["head"]["token"],
        idempotency_key="stage6-stale-undo",
    )
    restored = practice.get(value["practice_session"]["practice_session_id"])
    assert restored["availability"]["status"] == "review_changed"
    assert restored["attempt"]["range"] == value["attempt"]["range"]


def test_one_frame_custom_range_is_valid_and_end_frame_remains_exclusive(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-one-frame-create")
    session_id = value["practice_session"]["practice_session_id"]
    saved = practice.update(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage6-one-frame-save",
        state_mapping={
            "target_id": None,
            "custom_range": {"start_frame": 7_999, "end_frame": 8_000},
            "position_frame": 7_999,
            "loop_enabled": True,
            "rate_milli": 1_000,
            "count_in": "one_approved_bar",
        },
    )
    assert saved["attempt"]["range"] == {"start_frame": 7_999, "end_frame": 8_000}
    assert saved["attempt"]["position_frame"] == 7_999


def test_distinct_create_keys_converge_without_spending_receipt_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    first = practice.create(approval_id, idempotency_key="stage6-create-first")
    receipts_before = tuple(practice.store.receipts.glob("*.json"))
    monkeypatch.setattr(
        practice_store_module,
        "_MAX_RECEIPTS",
        len(receipts_before),
    )
    second = PracticeService(tmp_path).create(
        approval_id,
        idempotency_key="stage6-create-second",
    )
    repeated = PracticeService(tmp_path).create(
        approval_id,
        idempotency_key="stage6-create-first",
    )
    assert second["practice_session"] == first["practice_session"]
    assert second["attempt"] == first["attempt"]
    assert second["head"] == first["head"]
    assert repeated == first
    assert tuple(practice.store.receipts.glob("*.json")) == receipts_before


def test_completed_create_still_validates_idempotency_contract(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    created = practice.create(approval_id, idempotency_key="stage6-create-valid")
    for key in ("", "short", "x" * 201):
        with pytest.raises(PracticeError) as invalid:
            practice.create(approval_id, idempotency_key=key)
        assert invalid.value.code == "invalid_idempotency_key"

    practice.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-bound-to-update",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 0,
            "loop_enabled": False,
            "rate_milli": 1_000,
            "count_in": "off",
        },
    )
    with pytest.raises(PracticeError) as conflict:
        practice.create(
            approval_id,
            idempotency_key="stage6-bound-to-update",
        )
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(PracticeError) as wrong_fingerprint:
        practice.store.validate_creation_key(
            idempotency_key="stage6-create-valid",
            fingerprint="0" * 64,
        )
    assert wrong_fingerprint.value.code == "idempotency_conflict"


@pytest.mark.parametrize("failure_point", ("attempt", "head", "session"))
def test_interrupted_initial_publication_is_invisible_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    store = practice.store
    original_attempt = store._publish_attempt
    original_json = practice_store_module._publish_json

    if failure_point == "attempt":
        monkeypatch.setattr(
            store,
            "_publish_attempt",
            lambda _attempt: (_ for _ in ()).throw(OSError("injected attempt failure")),
        )
    else:
        target_parent = store.heads if failure_point == "head" else store.sessions

        def fail_at(path: Path, value: dict) -> None:
            if path.parent == target_parent:
                raise OSError(f"injected {failure_point} failure")
            original_json(path, value)

        monkeypatch.setattr(practice_store_module, "_publish_json", fail_at)

    with pytest.raises((OSError, PracticeError)):
        practice.create(approval_id, idempotency_key=f"stage6-crash-{failure_point}")
    assert practice.list(source_id=source_id) == []

    monkeypatch.setattr(store, "_publish_attempt", original_attempt)
    monkeypatch.setattr(practice_store_module, "_publish_json", original_json)
    recovered = PracticeService(tmp_path).create(
        approval_id,
        idempotency_key=f"stage6-recover-{failure_point}",
    )
    assert recovered["availability"]["status"] == "ready"
    assert len(PracticeService(tmp_path).list(source_id=source_id)) == 1


def test_practice_publication_fsyncs_records_and_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    original = durable_fs.os.fsync
    descriptors: list[int] = []

    def observed(descriptor: int) -> None:
        descriptors.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(durable_fs.os, "fsync", observed)
    practice.create(approval_id, idempotency_key="stage6-durable-create")
    assert len(descriptors) >= 8


def test_oversized_practice_record_is_rejected_before_publication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized.json"
    with pytest.raises(PracticeError) as oversized:
        practice_store_module._publish_json(
            path,
            {"payload": "x" * practice_store_module._MAX_RECORD_BYTES},
        )
    assert oversized.value.code == "practice_limit"
    assert not path.exists()


def test_linux_style_opath_is_not_used_for_durable_directory_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(durable_fs.os, "O_SEARCH", raising=False)
    monkeypatch.setattr(durable_fs.os, "O_PATH", 0x200000, raising=False)
    assert durable_fs._parent_open_flags(False) & 0x200000
    assert durable_fs._parent_open_flags(True) & 0x200000 == 0


def test_nonrepeating_runtime_stops_at_the_exclusive_range_end() -> None:
    controller = PracticePlaybackController(
        Timebase(8_000, 32_000),
        loop=FrameRange(8_000, 16_000),
        rate_milli=1_000,
        count_in_beats=0,
        repeat=False,
    )
    assert controller.start().playing
    completed = controller.advance_source_frames(8_000)
    assert completed.position_frame == 16_000
    assert completed.playing is False
    assert completed.completed_loops == 0


def test_practice_usage_reports_exact_private_record_counts_bytes_and_token(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    empty = practice.usage()
    assert set(empty["categories"]) == {
        "sessions",
        "heads",
        "attempts",
        "receipts",
        "recovery",
    }
    assert empty["categories"]["sessions"]["count"] == 0
    created = practice.create(approval_id, idempotency_key="stage6-usage-create")
    usage = practice.usage()
    assert usage["categories"]["sessions"]["count"] == 1
    assert usage["categories"]["heads"]["count"] == 1
    assert usage["categories"]["attempts"]["count"] == 1
    assert usage["categories"]["receipts"]["count"] == 1
    assert usage["categories"]["recovery"]["count"] == 0
    assert usage["categories"]["sessions"]["bytes"] == sum(
        path.stat().st_size for path in practice.store.sessions.glob("*.json")
    )
    assert usage["totals"]["bytes"] == sum(
        item["bytes"] for item in usage["categories"].values()
    )
    assert usage["token"] != empty["token"]

    updated = practice.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-usage-save",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 1,
            "loop_enabled": False,
            "rate_milli": 1_000,
            "count_in": "off",
        },
    )
    assert updated["head"]["generation"] == 1
    assert practice.usage()["token"] != usage["token"]


def test_practice_usage_near_limit_is_count_based_without_byte_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-near-limit")
    monkeypatch.setattr(practice_store_module, "_MAX_SESSIONS", 1)
    usage = practice.usage()
    sessions = usage["categories"]["sessions"]
    assert sessions == {
        "count": 1,
        "bytes": sessions["bytes"],
        "limit": 1,
        "remaining": 0,
        "near_limit": True,
    }
    assert usage["warnings"] == [
        "practice-session project limit reached: 0 records remain"
    ]
    assert usage["capabilities"]["prepare"] == {
        "allowed": False,
        "blocking_resources": ["sessions"],
    }
    assert "limit" not in usage["totals"]


@pytest.mark.parametrize(
    ("attempt_limit", "receipt_limit", "blocking", "warnings"),
    (
        (
            2,
            10,
            ["attempts"],
            ["saved-attempt project limit reached: 0 records remain"],
        ),
        (
            10,
            2,
            ["receipts"],
            ["saved-action project limit reached: 0 records remain"],
        ),
        (
            2,
            2,
            ["attempts", "receipts"],
            [
                "saved-attempt project limit reached: 0 records remain",
                "saved-action project limit reached: 0 records remain",
            ],
        ),
    ),
)
def test_final_saved_action_reports_exact_save_capability_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attempt_limit: int,
    receipt_limit: int,
    blocking: list[str],
    warnings: list[str],
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    created = practice.create(
        approval_id,
        idempotency_key="stage6-final-action-create",
    )
    monkeypatch.setattr(practice_store_module, "_MAX_ATTEMPTS", attempt_limit)
    monkeypatch.setattr(practice_store_module, "_MAX_RECEIPTS", receipt_limit)
    practice.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-final-action-save",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 0,
            "loop_enabled": False,
            "rate_milli": 1_000,
            "count_in": "off",
        },
    )
    usage = practice.usage()
    assert usage["categories"]["attempts"]["count"] == 2
    assert usage["categories"]["receipts"]["count"] == 2
    for name in blocking:
        assert usage["categories"][name]["remaining"] == 0
    assert usage["capabilities"]["save"] == {
        "allowed": False,
        "blocking_resources": blocking,
    }
    assert usage["warnings"] == warnings


def test_practice_reset_is_project_wide_idempotent_and_namespace_isolated(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    created = practice.create(approval_id, idempotency_key="stage6-reset-create")
    practice.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-reset-save",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 2_000,
            "loop_enabled": True,
            "rate_milli": 750,
            "count_in": "off",
        },
    )
    sentinel = practice.store.private_root / "reset-scope-sentinel"
    sentinel.write_bytes(b"non-practice-private-artifact")
    os.chmod(sentinel, 0o600)
    before = practice.usage()
    result = practice.reset(
        expected_token=before["token"],
        idempotency_key="stage6-reset-all",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert result["removed"]["categories"]["sessions"]["count"] == 1
    assert result["removed"]["categories"]["attempts"]["count"] == 2
    assert result["usage"]["categories"]["sessions"]["count"] == 0
    assert result["usage"]["categories"]["heads"]["count"] == 0
    assert result["usage"]["categories"]["attempts"]["count"] == 0
    assert result["usage"]["categories"]["receipts"]["count"] == 0
    assert sentinel.read_bytes() == b"non-practice-private-artifact"
    assert practice.list(source_id=source_id) == []

    repeated = practice.reset(
        expected_token=before["token"],
        idempotency_key="stage6-reset-all",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert repeated["removed"] == result["removed"]
    assert repeated["usage"] == result["usage"]
    recreated = practice.create(
        approval_id,
        idempotency_key="stage6-reset-recreate",
    )
    assert recreated["availability"]["status"] == "ready"
    replay_after_new_history = practice.reset(
        expected_token=before["token"],
        idempotency_key="stage6-reset-all",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert replay_after_new_history["replayed"] is True
    assert replay_after_new_history["usage"]["categories"]["sessions"]["count"] == 1


def test_practice_reset_rejects_confirmation_stale_token_and_key_reuse(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    usage = practice.usage()
    with pytest.raises(PracticeError) as confirmation:
        practice.reset(
            expected_token=usage["token"],
            idempotency_key="stage6-reset-confirmation",
            confirmation="clear all practice history",
        )
    assert confirmation.value.code == "practice_confirmation_required"
    practice.create(approval_id, idempotency_key="stage6-reset-stale-create")
    with pytest.raises(PracticeError) as stale:
        practice.reset(
            expected_token=usage["token"],
            idempotency_key="stage6-reset-stale",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
    assert stale.value.code == "practice_conflict"
    current = practice.usage()
    practice.reset(
        expected_token=current["token"],
        idempotency_key="stage6-reset-key",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    with pytest.raises(PracticeError) as reused:
        practice.reset(
            expected_token=practice.usage()["token"],
            idempotency_key="stage6-reset-key",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
    assert reused.value.code == "idempotency_conflict"


@pytest.mark.parametrize("attack", ("symlink", "hardlink", "oversized", "mode"))
def test_practice_usage_rejects_unsafe_record_entries(
    tmp_path: Path,
    attack: str,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key=f"stage6-usage-{attack}")
    original = next(practice.store.sessions.glob("*.json"))
    unsafe = practice.store.sessions / "unsafe.json"
    if attack == "symlink":
        unsafe.symlink_to(original)
    elif attack == "hardlink":
        os.link(original, unsafe)
    elif attack == "oversized":
        unsafe.write_bytes(b"x" * (practice_store_module._MAX_RECORD_BYTES + 1))
        os.chmod(unsafe, 0o600)
    else:
        unsafe.write_text("{}\n")
        os.chmod(unsafe, 0o644)
    with pytest.raises(PracticeError) as rejected:
        practice.usage()
    assert rejected.value.code == "practice_storage_integrity"


def test_interrupted_reset_restarts_to_clean_history_and_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-crash-reset-create")
    before = practice.usage()
    original = practice_store_module._remove_private_tree
    monkeypatch.setattr(
        practice_store_module,
        "_remove_private_tree",
        lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(OSError("injected purge crash")),
    )
    with pytest.raises(PracticeError) as interrupted:
        practice.reset(
            expected_token=before["token"],
            idempotency_key="stage6-crash-reset",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
    assert interrupted.value.code == "practice_cleanup_incomplete"
    degraded = practice.usage()
    assert degraded["categories"]["sessions"]["count"] == 0
    assert degraded["reset_allowed"] is False
    monkeypatch.setattr(practice_store_module, "_remove_private_tree", original)
    recovered = PracticeService(tmp_path)
    assert recovered.list(source_id=source_id) == []
    repeated = recovered.reset(
        expected_token=before["token"],
        idempotency_key="stage6-crash-reset",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert repeated["removed"]["categories"]["sessions"]["count"] == 1
    assert repeated["usage"]["categories"]["sessions"]["count"] == 0


def test_mid_purge_reset_restarts_after_one_descriptor_relative_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    created = practice.create(approval_id, idempotency_key="stage6-mid-purge-create")
    practice.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-mid-purge-save",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 1_234,
            "loop_enabled": True,
            "rate_milli": 750,
            "count_in": "off",
        },
    )
    sentinel = practice.store.private_root / "mid-purge-sentinel"
    sentinel.write_bytes(b"non-practice-private-artifact")
    os.chmod(sentinel, 0o600)
    before = practice.usage()
    old_file_count = sum(
        1 for path in practice.store.root.rglob("*") if path.is_file()
    )
    original_unlink = practice_store_module.os.unlink
    successful_unlinks = 0

    def fail_after_first_unlink(*args, **kwargs) -> None:
        nonlocal successful_unlinks
        if (
            kwargs.get("dir_fd") is None
            or str(args[0]).startswith(".")
        ):
            original_unlink(*args, **kwargs)
            return
        if successful_unlinks == 0:
            original_unlink(*args, **kwargs)
            successful_unlinks += 1
        raise OSError("injected after first descriptor-relative purge unlink")

    monkeypatch.setattr(practice_store_module.os, "unlink", fail_after_first_unlink)
    try:
        with pytest.raises(PracticeError) as interrupted:
            practice.reset(
                expected_token=before["token"],
                idempotency_key="stage6-mid-purge-reset",
                confirmation="CLEAR ALL PRACTICE HISTORY",
            )
        assert interrupted.value.code == "practice_cleanup_incomplete"
        assert interrupted.value.retryable is True
        degraded = practice.usage()
        assert degraded["categories"]["sessions"]["count"] == 0
        assert degraded["categories"]["heads"]["count"] == 0
        assert degraded["categories"]["attempts"]["count"] == 0
        assert degraded["categories"]["receipts"]["count"] == 0
        assert degraded["reset_allowed"] is False
        receipt_path = next(practice.store.reset_receipts.glob("*.json"))
        receipt = json.loads(receipt_path.read_text())
        assert receipt["phase"] == "activated"
        quarantine = (
            practice.store.private_root
            / f".practice-quarantine-{receipt['reset_id']}"
        )
        remaining_file_count = sum(
            1 for path in quarantine.rglob("*") if path.is_file()
        )
        assert successful_unlinks == 1
        assert 0 < remaining_file_count < old_file_count
    finally:
        monkeypatch.setattr(practice_store_module.os, "unlink", original_unlink)

    recovered = PracticeService(tmp_path)
    assert recovered.list(source_id=source_id) == []
    usage = recovered.usage()
    assert usage["categories"]["sessions"]["count"] == 0
    assert usage["categories"]["heads"]["count"] == 0
    assert usage["categories"]["attempts"]["count"] == 0
    assert usage["categories"]["receipts"]["count"] == 0
    assert usage["reset_allowed"] is True
    assert not quarantine.exists()
    assert json.loads(receipt_path.read_text())["phase"] == "complete"
    assert sentinel.read_bytes() == b"non-practice-private-artifact"
    replayed = recovered.reset(
        expected_token=before["token"],
        idempotency_key="stage6-mid-purge-reset",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert replayed["replayed"] is True
    for name, removed in replayed["removed"]["categories"].items():
        assert removed == {
            "count": before["categories"][name]["count"],
            "bytes": before["categories"][name]["bytes"],
        }
    assert replayed["usage"]["categories"]["sessions"]["count"] == 0


def test_restart_cleanup_preserves_newer_active_practice_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-live-recovery-old")
    sentinel = practice.store.private_root / "live-recovery-sentinel"
    sentinel.write_bytes(b"non-practice-private-artifact")
    os.chmod(sentinel, 0o600)
    before = practice.usage()
    original_remove = practice_store_module._remove_private_tree
    monkeypatch.setattr(
        practice_store_module,
        "_remove_private_tree",
        lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(OSError("injected persistent cleanup block")),
    )
    with pytest.raises(PracticeError) as interrupted:
        practice.reset(
            expected_token=before["token"],
            idempotency_key="stage6-live-recovery-reset",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
    assert interrupted.value.code == "practice_cleanup_incomplete"

    blocked = PracticeService(tmp_path)
    created = blocked.create(
        approval_id,
        idempotency_key="stage6-live-recovery-new",
    )
    saved = blocked.update(
        created["practice_session"]["practice_session_id"],
        expected_token=created["head"]["token"],
        idempotency_key="stage6-live-recovery-save",
        state_mapping={
            "target_id": created["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 1_234,
            "loop_enabled": True,
            "rate_milli": 750,
            "count_in": "off",
        },
    )
    degraded = blocked.usage()
    assert degraded["categories"]["sessions"]["count"] == 1
    assert degraded["categories"]["heads"]["count"] == 1
    assert degraded["categories"]["attempts"]["count"] == 2
    assert degraded["categories"]["receipts"]["count"] == 2
    assert degraded["reset_allowed"] is False
    live_snapshot = {
        path.relative_to(blocked.store.root).as_posix(): path.read_bytes()
        for path in sorted(blocked.store.root.rglob("*"))
        if path.is_file()
    }
    expected_head = saved["head"]
    expected_attempt = saved["attempt"]
    receipt_path = next(blocked.store.reset_receipts.glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    quarantine = (
        blocked.store.private_root
        / f".practice-quarantine-{receipt['reset_id']}"
    )
    assert receipt["phase"] == "activated"
    assert quarantine.exists()

    monkeypatch.setattr(
        practice_store_module,
        "_remove_private_tree",
        original_remove,
    )
    recovered = PracticeService(tmp_path)
    recovered_snapshot = {
        path.relative_to(recovered.store.root).as_posix(): path.read_bytes()
        for path in sorted(recovered.store.root.rglob("*"))
        if path.is_file()
    }
    assert recovered_snapshot == live_snapshot
    restored = recovered.get(created["practice_session"]["practice_session_id"])
    assert restored["head"] == expected_head
    assert restored["attempt"] == expected_attempt
    assert restored["attempt"]["position_frame"] == 1_234
    assert restored["attempt"]["loop_enabled"] is True
    assert restored["attempt"]["rate_milli"] == 750
    assert restored["head"]["generation"] == 1
    assert len(recovered.list(source_id=source_id)) == 1
    assert not quarantine.exists()
    assert json.loads(receipt_path.read_text())["phase"] == "complete"
    assert sentinel.read_bytes() == b"non-practice-private-artifact"

    replayed = recovered.reset(
        expected_token=before["token"],
        idempotency_key="stage6-live-recovery-reset",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    assert replayed["replayed"] is True
    assert replayed["usage"]["categories"]["sessions"]["count"] == 1
    assert {
        path.relative_to(recovered.store.root).as_posix(): path.read_bytes()
        for path in sorted(recovered.store.root.rglob("*"))
        if path.is_file()
    } == live_snapshot


@pytest.mark.parametrize(
    "fault",
    ("before_quarantine", "during_recreation", "before_activation", "after_purge"),
)
def test_reset_crash_points_converge_to_one_empty_visible_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key=f"stage6-crash-{fault}-create")
    before = practice.usage()
    original_rename = practice_store_module._rename_directory
    original_ensure = practice_store_module._ensure_private_directory
    original_replace = practice_store_module._replace_json
    injected = False

    def fail_rename(*_args) -> None:
        raise OSError("injected before quarantine")

    def fail_recreation(path: Path) -> None:
        nonlocal injected
        if not injected and path == practice.store.sessions:
            injected = True
            raise OSError("injected during recreation")
        original_ensure(path)

    def fail_replace(path: Path, value: dict) -> None:
        nonlocal injected
        phase = value.get("phase")
        should_fail = (
            (fault == "before_activation" and phase == "activated")
            or (fault == "after_purge" and phase == "complete")
        )
        if not injected and should_fail:
            injected = True
            raise OSError(f"injected {fault}")
        original_replace(path, value)

    if fault == "before_quarantine":
        monkeypatch.setattr(practice_store_module, "_rename_directory", fail_rename)
    elif fault == "during_recreation":
        monkeypatch.setattr(
            practice_store_module,
            "_ensure_private_directory",
            fail_recreation,
        )
    else:
        monkeypatch.setattr(practice_store_module, "_replace_json", fail_replace)

    with pytest.raises(OSError):
        practice.reset(
            expected_token=before["token"],
            idempotency_key=f"stage6-crash-{fault}",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )

    monkeypatch.setattr(practice_store_module, "_rename_directory", original_rename)
    monkeypatch.setattr(
        practice_store_module,
        "_ensure_private_directory",
        original_ensure,
    )
    monkeypatch.setattr(practice_store_module, "_replace_json", original_replace)
    recovered = PracticeService(tmp_path)
    assert recovered.list(source_id=source_id) == []
    assert recovered.usage()["categories"]["sessions"]["count"] == 0
    assert not tuple(recovered.store.private_root.glob(".practice-quarantine-*"))


def test_reset_serializes_against_complete_practice_operations(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-lock-create")
    usage = practice.usage()
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def hold_read() -> None:
        with practice.store.lifecycle():
            entered.set()
            release.wait(timeout=2)

    def reset() -> None:
        practice.reset(
            expected_token=usage["token"],
            idempotency_key="stage6-lock-reset",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
        completed.set()

    reader = threading.Thread(target=hold_read)
    clearer = threading.Thread(target=reset)
    reader.start()
    assert entered.wait(timeout=1)
    clearer.start()
    assert not completed.wait(timeout=0.05)
    release.set()
    reader.join(timeout=2)
    clearer.join(timeout=2)
    assert completed.is_set()


def test_reset_receipt_rejects_private_payload_injection(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-receipt-create")
    before = practice.usage()
    practice.reset(
        expected_token=before["token"],
        idempotency_key="stage6-receipt-reset",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    receipt = next(practice.store.reset_receipts.glob("*.json"))
    value = json.loads(receipt.read_text())
    value["removed"]["private_path"] = "/private/secret.wav"
    receipt.write_text(json.dumps(value))
    os.chmod(receipt, 0o600)
    with pytest.raises(PracticeError) as rejected:
        practice.reset(
            expected_token=before["token"],
            idempotency_key="stage6-receipt-reset",
            confirmation="CLEAR ALL PRACTICE HISTORY",
        )
    assert rejected.value.code == "practice_storage_integrity"


def test_reset_recovery_never_purges_replacement_quarantine(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-replacement-create")
    before = practice.usage()
    practice.reset(
        expected_token=before["token"],
        idempotency_key="stage6-replacement-reset",
        confirmation="CLEAR ALL PRACTICE HISTORY",
    )
    receipt_path = next(practice.store.reset_receipts.glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    receipt["phase"] = "activated"
    receipt_path.write_text(json.dumps(receipt))
    os.chmod(receipt_path, 0o600)
    quarantine = (
        practice.store.private_root
        / f".practice-quarantine-{receipt['reset_id']}"
    )
    quarantine.mkdir(mode=0o700)
    sentinel = quarantine / "unrelated-private-note"
    sentinel.write_text("preserve replacement")
    os.chmod(sentinel, 0o600)

    recovered = PracticeService(tmp_path)
    assert sentinel.read_text() == "preserve replacement"
    usage = recovered.usage()
    assert usage["categories"]["sessions"]["count"] == 0
    assert usage["reset_allowed"] is False
