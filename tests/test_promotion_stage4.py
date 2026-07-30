from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
import chordatlas.promotion.store as promotion_store_module

from chordatlas.analysis import (
    AnalysisSpec,
    AnalysisStore,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    EngineRef,
)
from chordatlas.media import FrameRange, ProjectMediaStore, Timebase
from chordatlas.models import SongChart
from chordatlas.promotion import PromotionError, PromotionService
from chordatlas.promotion.mapper import build_draft
from chordatlas.promotion.models import MappingConfig
from chordatlas.review import (
    ReviewEdit,
    ReviewError,
    ReviewService,
    apply_edit,
    root_timeline,
)
from chordatlas.schema import validate_chart_schema


def _timeline() -> ChordCandidateTimeline:
    timebase = Timebase(1_000, 3_000)

    def segment(ordinal: int, start: int, end: int, label: str) -> ChordSegment:
        return ChordSegment(
            ordinal,
            FrameRange(start, end),
            "chord",
            (ChordCandidate(label, label, 1, 700_000),),
            0,
        )

    return ChordCandidateTimeline.create(
        spec_id="sha256:" + ("a" * 64),
        timebase=timebase,
        analyzed_range=FrameRange(0, 3_000),
        result_kind="candidates",
        beats=(0, 500, 1_000, 1_500, 2_000, 2_500),
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=(
            segment(0, 0, 1_000, "C:maj"),
            segment(1, 1_000, 2_000, "G:maj"),
            segment(2, 2_000, 3_000, "A:min"),
        ),
        engine=EngineRef(),
    )


def _ready(
    tmp_path: Path,
    *,
    all_no_chord: bool = False,
    add_section: bool = True,
) -> tuple[dict, ReviewService]:
    ProjectMediaStore.initialize(tmp_path)
    analysis = AnalysisStore.initialize(tmp_path)
    base = _timeline()
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
        tempo_hypotheses=(),
        key_hypotheses=(),
        segments=base.segments,
        engine=base.engine,
    )
    run = analysis.create_run(spec, source_id="src_" + ("c" * 32))
    analysis.transition(run.id, "running")
    analysis.publish_success(run.id, base)
    review = ReviewService(tmp_path)
    value = review.create(run.id, idempotency_key="stage4-create-review")
    session_id = value["session"]["session_id"]
    for index, segment in enumerate(value["timeline"]["segments"]):
        value = review.apply(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key=f"stage4-accept-label-{index}",
            kind="set_no_chord" if all_no_chord else "accept_current",
            parameters={"segment_id": segment["id"]},
        )
    value = review.apply(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage4-accept-boundaries",
        kind="accept_boundaries",
        parameters={},
    )
    if add_section:
        value = review.apply(
            session_id,
            expected_token=value["head"]["token"],
            idempotency_key="stage4-add-chorus",
            kind="add_section",
            parameters={"frame": 2_000, "label": "Chorus"},
        )
    value = review.apply(
        session_id,
        expected_token=value["head"]["token"],
        idempotency_key="stage4-finish-review",
        kind="finish_review",
        parameters={},
    )
    return value, review


def _config() -> dict:
    return {
        "mapping_version": "songchart-explicit-grid-v1",
        "title": "Synthetic progression",
        "artist": None,
        "key": None,
        "tuning": "Standard",
        "capo": "None",
        "chart_version": "1.0",
        "meter_numerator": 4,
        "beat_unit": 4,
        "measure_boundaries_frames": [0, 1_000, 2_000, 3_000],
        "pickup_policy": "full_coverage_confirmed",
        "default_section_name": "Song",
        "notation_mode": "sounding",
        "diagram_policy": "built_in_standard_only",
        "loss_policy": "allow_declared",
        "guitar_decisions": [
            {
                "chord": "Am",
                "inversion_reviewed": True,
                "voicing": "built_in",
                "playability": "playable",
                "note": None,
            },
            {
                "chord": "C",
                "inversion_reviewed": True,
                "voicing": "built_in",
                "playability": "playable",
                "note": None,
            },
            {
                "chord": "G",
                "inversion_reviewed": True,
                "voicing": "built_in",
                "playability": "playable",
                "note": None,
            },
        ],
    }


def _material_ids(preview: dict) -> tuple[str, ...]:
    return tuple(
        sorted(
            item["id"]
            for item in preview["issues"]
            if item["severity"] == "material"
        )
    )


def _reviewed_timeline(base, *, middle_kind: str, middle_value: str | None = None):
    timeline = root_timeline(base)
    raw = root_timeline(base)
    for index, segment in enumerate(tuple(timeline.segments)):
        kind = "accept_current"
        parameters = {"segment_id": timeline.segments[index].id}
        if index == 1 and middle_kind == "set_no_chord":
            kind = "set_no_chord"
        elif index == 1 and middle_kind == "set_label":
            kind = "set_label"
            parameters["label"] = middle_value
        timeline = apply_edit(
            timeline,
            ReviewEdit.create(kind, parameters),
            base=base,
            raw_root=raw,
        )
    timeline = apply_edit(
        timeline,
        ReviewEdit.create("accept_boundaries", {}),
        base=base,
        raw_root=raw,
    )
    return apply_edit(
        timeline,
        ReviewEdit.create("finish_review", {}),
        base=base,
        raw_root=raw,
    )


def test_preview_is_deterministic_valid_and_preserves_exact_private_timing(
    tmp_path: Path,
) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path)
    args = (
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
    )
    first = service.preview(
        *args,
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    second = service.preview(
        *args,
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )

    assert first == second
    assert first["chart"]["schema_version"] == "1.0.0"
    assert [bar["chords"] for section in first["chart"]["sections"] for bar in section["bars"]] == [
        ["C"],
        ["G"],
        ["Am"],
    ]
    assert [item["section_name"] for item in first["timing_map"]] == [
        "Song",
        "Song",
        "Chorus",
    ]
    assert "## Chord Reference" in first["renders"]["markdown"]
    assert "e|--0--" in first["renders"]["markdown"]
    assert validate_chart_schema(SongChart.from_mapping(first["chart"])).passed


def test_approval_pins_revision_survives_head_movement_and_revocation(
    tmp_path: Path,
) -> None:
    ready, review = _ready(tmp_path)
    service = PromotionService(
        tmp_path,
        clock=iter(
            (
                "2026-07-23T12:00:00+00:00",
                "2026-07-23T12:01:00+00:00",
                "2026-07-23T12:02:00+00:00",
            )
        ).__next__,
    )
    session_id = ready["session"]["session_id"]
    preview = service.preview(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    approved = service.approve(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-approve-exact-0001",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    repeated = service.approve(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-approve-exact-0001",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    assert approved == repeated
    approval_id = approved["approval"]["approval_id"]
    result_id = approved["approval"]["result_id"]

    moved = review.undo(
        session_id,
        expected_token=ready["head"]["token"],
        idempotency_key="stage4-head-move-after-approval",
    )
    assert moved["head"]["revision_id"] != ready["head"]["revision_id"]
    exports = service.exports(approval_id)
    assert "Synthetic progression" in exports["markdown"]
    assert json.loads(exports["json"])["schema_version"] == "1.0.0"

    revoked = service.revoke(
        approval_id,
        expected_token=approved["approval"]["token"],
        idempotency_key="stage4-revoke-exact-0001",
        reason="mistake",
    )
    assert revoked["approval"]["status"] == "revoked"
    assert revoked["approval"]["result_id"] == result_id
    assert revoked["approval"]["revocation"]["prior_exports_not_retracted"] is True
    with pytest.raises(PromotionError) as denied:
        service.exports(approval_id)
    assert denied.value.code == "approval_revoked"


def test_stale_head_unacknowledged_loss_and_off_grid_section_fail_closed(
    tmp_path: Path,
) -> None:
    ready, review = _ready(tmp_path)
    service = PromotionService(tmp_path)
    session_id = ready["session"]["session_id"]
    preview = service.preview(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    with pytest.raises(PromotionError) as unacknowledged:
        service.approve(
            session_id,
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            idempotency_key="stage4-missing-acks-0001",
            config_mapping=_config(),
            expected_spec_id=preview["spec_id"],
            expected_result_id=preview["result_id"],
            expected_issue_digest=preview["issue_digest"],
            acknowledged_issue_ids=(),
        )
    assert unacknowledged.value.code == "acknowledgement_required"

    moved = review.undo(
        session_id,
        expected_token=ready["head"]["token"],
        idempotency_key="stage4-stale-head-undo",
    )
    with pytest.raises(ReviewError) as stale:
        service.preview(
            session_id,
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            config_mapping=_config(),
        )
    assert getattr(stale.value, "code", None) == "review_conflict"

    restored = review.redo(
        session_id,
        expected_token=moved["head"]["token"],
        idempotency_key="stage4-stale-head-redo",
        requested_revision_id=ready["head"]["revision_id"],
    )
    config = _config()
    config["measure_boundaries_frames"] = [0, 1_500, 2_500, 3_000]
    with pytest.raises(PromotionError) as off_grid:
        service.preview(
            session_id,
            restored["head"]["revision_id"],
            expected_head_token=restored["head"]["token"],
            config_mapping=config,
        )
    assert off_grid.value.code == "section_off_grid"


def test_public_exports_exclude_all_private_transcription_fields(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    approved = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-privacy-approve",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    payload = "\n".join(service.exports(approved["approval"]["approval_id"]).values())
    forbidden = (
        str(tmp_path),
        "src_",
        "run_",
        "review_",
        "rrv_",
        "apr_",
        "sha256:",
        "confidence_ppm",
        "alternatives",
        "private_locator",
        "model_artifact",
        "authorization_basis",
    )
    assert all(item not in payload for item in forbidden)


def test_promotion_fixture_is_authorized_synthetic_and_lyrics_free() -> None:
    path = Path(__file__).parent / "fixtures" / "promotion" / "fixture-manifest.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["authorization"] == {
        "kind": "synthetic",
        "license": "CC0-1.0",
        "redistributable": True,
    }
    assert fixture["measure_boundaries_frames"] == [0, 1_000, 2_000, 3_000]
    assert fixture["copyrighted_lyrics"] is False
    assert "lyrics" not in json.dumps(fixture["expected_chords"]).lower()


def test_half_open_cross_bar_projection_preserves_nc_and_exact_private_spans() -> None:
    base = _timeline()
    reviewed = _reviewed_timeline(base, middle_kind="set_no_chord")
    config = _config()
    config["measure_boundaries_frames"] = [0, 1_500, 3_000]
    config["guitar_decisions"] = [
        item for item in config["guitar_decisions"] if item["chord"] != "G"
    ]
    draft = build_draft(reviewed, base, MappingConfig.from_mapping(config))

    assert [bar.chords for section in draft.chart.sections for bar in section.bars] == [
        ("C", "N.C."),
        ("N.C.", "Am"),
    ]
    assert draft.timing_map[0].chord_spans[-1] == ("N.C.", 1_000, 1_500)
    assert draft.timing_map[1].chord_spans[0] == ("N.C.", 1_500, 2_000)


def test_inversion_and_missing_shape_require_explicit_guitar_resolution() -> None:
    base = _timeline()
    reviewed = _reviewed_timeline(
        base,
        middle_kind="set_label",
        middle_value="D/F#",
    )
    config = _config()
    for item in config["guitar_decisions"]:
        if item["chord"] == "G":
            item["chord"] = "D/F#"
            item["inversion_reviewed"] = False
    config["guitar_decisions"].sort(key=lambda item: item["chord"])
    with pytest.raises(PromotionError) as inversion:
        build_draft(reviewed, base, MappingConfig.from_mapping(config))
    assert inversion.value.code == "inversion_unreviewed"

    for item in config["guitar_decisions"]:
        if item["chord"] == "D/F#":
            item["inversion_reviewed"] = True
    draft = build_draft(reviewed, base, MappingConfig.from_mapping(config))
    assert "D/F#" in draft.chart.used_chords

    unsupported = _reviewed_timeline(
        base,
        middle_kind="set_label",
        middle_value="C#maj7",
    )
    for item in config["guitar_decisions"]:
        if item["chord"] == "D/F#":
            item["chord"] = "C#maj7"
            item["inversion_reviewed"] = True
            item["voicing"] = "built_in"
    config["guitar_decisions"].sort(key=lambda item: item["chord"])
    with pytest.raises(PromotionError) as missing:
        build_draft(unsupported, base, MappingConfig.from_mapping(config))
    assert missing.value.code == "voicing_unavailable"
    for item in config["guitar_decisions"]:
        if item["chord"] == "C#maj7":
            item["voicing"] = "manual_required"
            item["note"] = "Choose and verify a Standard-tuning shape."
    resolved = build_draft(unsupported, base, MappingConfig.from_mapping(config))
    assert any(item.code == "guitar.no_builtin_shape" for item in resolved.issues)


def test_idempotency_conflict_and_interrupted_visibility_retry_converge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    session_id = ready["session"]["session_id"]
    preview = service.preview(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    original = service.store._append_event
    attempts = 0

    def interrupt_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected publication interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.store, "_append_event", interrupt_once)
    with pytest.raises(OSError):
        service.approve(
            session_id,
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            idempotency_key="stage4-crash-retry-0001",
            config_mapping=_config(),
            expected_spec_id=preview["spec_id"],
            expected_result_id=preview["result_id"],
            expected_issue_digest=preview["issue_digest"],
            acknowledged_issue_ids=_material_ids(preview),
        )
    approved = service.approve(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-crash-retry-0001",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    assert approved["approval"]["status"] == "active"

    changed = _config()
    changed["title"] = "Different title"
    changed_preview = service.preview(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=changed,
    )
    with pytest.raises(PromotionError) as conflict:
        service.approve(
            session_id,
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            idempotency_key="stage4-crash-retry-0001",
            config_mapping=changed,
            expected_spec_id=changed_preview["spec_id"],
            expected_result_id=changed_preview["result_id"],
            expected_issue_digest=changed_preview["issue_digest"],
            acknowledged_issue_ids=_material_ids(changed_preview),
        )
    assert conflict.value.code == "idempotency_conflict"


def test_two_concurrent_approvals_have_one_active_winner(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    outcomes: list[str] = []

    def approve(key: str) -> None:
        try:
            service.approve(
                ready["session"]["session_id"],
                ready["head"]["revision_id"],
                expected_head_token=ready["head"]["token"],
                    idempotency_key=key,
                    config_mapping=_config(),
                    expected_spec_id=preview["spec_id"],
                    expected_result_id=preview["result_id"],
                    expected_issue_digest=preview["issue_digest"],
                    acknowledged_issue_ids=_material_ids(preview),
            )
            outcomes.append("approved")
        except PromotionError as error:
            outcomes.append(error.code)

    threads = [
        threading.Thread(target=approve, args=(f"stage4-race-key-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["active_approval_exists", "approved"]


def test_revocation_allows_new_approval_over_same_result(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(
        tmp_path,
        clock=lambda: "2026-07-23T12:00:00+00:00",
    )
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    first = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-reapprove-first",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    service.revoke(
        first["approval"]["approval_id"],
        expected_token=first["approval"]["token"],
        idempotency_key="stage4-reapprove-revoke",
        reason="superseded",
    )
    second = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-reapprove-second",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert second["approval"]["token"] != first["approval"]["token"]
    assert second["approval"]["result_id"] == first["approval"]["result_id"]
    with pytest.raises(PromotionError) as stale_activation:
        service.revoke(
            first["approval"]["approval_id"],
            expected_token=first["approval"]["token"],
            idempotency_key="stage4-stale-activation-revoke",
            reason="mistake",
        )
    assert stale_activation.value.code == "approval_not_active"
    assert service.get(second["approval"]["approval_id"])["approval"]["status"] == "active"


def test_replayed_approval_key_cannot_reactivate_revoked_approval(
    tmp_path: Path,
) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(
        tmp_path,
        clock=lambda: "2026-07-23T12:00:00+00:00",
    )
    session_id = ready["session"]["session_id"]
    revision_id = ready["head"]["revision_id"]
    head_token = ready["head"]["token"]
    preview = service.preview(
        session_id,
        revision_id,
        expected_head_token=head_token,
        config_mapping=_config(),
    )
    approval_args = {
        "expected_head_token": head_token,
        "config_mapping": _config(),
        "expected_spec_id": preview["spec_id"],
        "expected_result_id": preview["result_id"],
        "expected_issue_digest": preview["issue_digest"],
        "acknowledged_issue_ids": _material_ids(preview),
    }
    first = service.approve(
        session_id,
        revision_id,
        idempotency_key="stage4-replay-original",
        **approval_args,
    )
    first_id = first["approval"]["approval_id"]
    service.revoke(
        first_id,
        expected_token=first["approval"]["token"],
        idempotency_key="stage4-replay-revoke",
        reason="withdrawn",
    )

    with pytest.raises(PromotionError) as replay:
        service.approve(
            session_id,
            revision_id,
            idempotency_key="stage4-replay-original",
            **approval_args,
        )
    assert replay.value.code == "idempotency_expired"
    assert service.get(first_id)["approval"]["status"] == "revoked"
    assert [
        event["kind"] for event in service.store._events_for(session_id)
    ] == ["approved", "revoked"]

    fresh = service.approve(
        session_id,
        revision_id,
        idempotency_key="stage4-replay-fresh",
        **approval_args,
    )
    assert fresh["approval"]["approval_id"] != first_id
    assert fresh["approval"]["status"] == "active"


def test_private_record_hardlink_and_symlink_lock_fail_closed(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    approved = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-hardlink-approve",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    approval_id = approved["approval"]["approval_id"]
    approval_path = service.store.approvals / f"{approval_id}.json"
    os.link(approval_path, service.store.root / "linked-approval.json")
    with pytest.raises(PromotionError) as linked:
        service.get(approval_id)
    assert linked.value.code == "promotion_storage_integrity"

    other_root = tmp_path / "other"
    other_root.mkdir()
    other_ready, _ = _ready(other_root)
    other_service = PromotionService(other_root)
    target = other_root / "unsafe-lock-target"
    target.write_text("sentinel", encoding="utf-8")
    lock = (
        other_service.store.locks
        / f"{other_ready['session']['session_id']}.lock"
    )
    lock.symlink_to(target)
    other_preview = other_service.preview(
        other_ready["session"]["session_id"],
        other_ready["head"]["revision_id"],
        expected_head_token=other_ready["head"]["token"],
        config_mapping=_config(),
    )
    with pytest.raises(PromotionError) as symlinked:
        other_service.approve(
            other_ready["session"]["session_id"],
            other_ready["head"]["revision_id"],
            expected_head_token=other_ready["head"]["token"],
            idempotency_key="stage4-symlink-lock",
            config_mapping=_config(),
            expected_spec_id=other_preview["spec_id"],
            expected_result_id=other_preview["result_id"],
            expected_issue_digest=other_preview["issue_digest"],
            acknowledged_issue_ids=_material_ids(other_preview),
        )
    assert symlinked.value.code == "promotion_storage_integrity"


def test_promotion_publication_rejects_intermediate_project_ancestor_replacement(
    tmp_path: Path,
) -> None:
    container = tmp_path / "selected"
    project = container / "project"
    project.mkdir(parents=True)
    ready, _review = _ready(project)
    service = PromotionService(project)
    container.rename(tmp_path / "selected-original")
    project.mkdir(parents=True)
    replacement = project / ".chordatlas"
    replacement.mkdir(mode=0o700)

    with pytest.raises(PromotionError) as rejected:
        service.store._publish(
            service.store.approvals / ("apr_" + ("a" * 64) + ".json"),
            {"session_id": ready["session"]["session_id"]},
        )

    assert rejected.value.code == "promotion_storage_integrity"
    assert list(replacement.iterdir()) == []


def test_approval_rejects_same_issue_set_for_an_unseen_changed_result(
    tmp_path: Path,
) -> None:
    ready, review = _ready(tmp_path)
    service = PromotionService(tmp_path)
    session_id = ready["session"]["session_id"]
    config = _config()
    config["measure_boundaries_frames"] = [0, 2_000, 3_000]
    first = service.preview(
        session_id,
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=config,
    )
    in_review = review.undo(
        session_id,
        expected_token=ready["head"]["token"],
        idempotency_key="stage4-preview-change-undo",
    )
    segments = in_review["timeline"]["segments"]
    changed = review.apply(
        session_id,
        expected_token=in_review["head"]["token"],
        idempotency_key="stage4-preview-change-boundary",
        kind="move_boundary",
        parameters={
            "left_segment_id": segments[0]["id"],
            "right_segment_id": segments[1]["id"],
            "frame": 1_500,
        },
    )
    changed = review.apply(
        session_id,
        expected_token=changed["head"]["token"],
        idempotency_key="stage4-preview-change-finish",
        kind="finish_review",
        parameters={},
    )
    second = service.preview(
        session_id,
        changed["head"]["revision_id"],
        expected_head_token=changed["head"]["token"],
        config_mapping=config,
    )
    assert first["result_id"] != second["result_id"]
    assert first["issue_digest"] == second["issue_digest"]
    with pytest.raises(PromotionError) as mismatch:
        service.approve(
            session_id,
            changed["head"]["revision_id"],
            expected_head_token=changed["head"]["token"],
            idempotency_key="stage4-unseen-result",
            config_mapping=config,
            expected_spec_id=first["spec_id"],
            expected_result_id=first["result_id"],
            expected_issue_digest=first["issue_digest"],
            acknowledged_issue_ids=_material_ids(first),
        )
    assert mismatch.value.code == "preview_mismatch"


def test_repeated_same_label_sections_remain_distinct_occurrences() -> None:
    base = _timeline()
    reviewed = root_timeline(base)
    raw = root_timeline(base)
    for segment in tuple(reviewed.segments):
        reviewed = apply_edit(
            reviewed,
            ReviewEdit.create(
                "accept_current",
                {"segment_id": segment.id},
            ),
            base=base,
            raw_root=raw,
        )
    reviewed = apply_edit(
        reviewed,
        ReviewEdit.create("accept_boundaries", {}),
        base=base,
        raw_root=raw,
    )
    for frame in (1_000, 2_000):
        reviewed = apply_edit(
            reviewed,
            ReviewEdit.create(
                "add_section",
                {"frame": frame, "label": "Verse"},
            ),
            base=base,
            raw_root=raw,
        )
    reviewed = apply_edit(
        reviewed,
        ReviewEdit.create("finish_review", {}),
        base=base,
        raw_root=raw,
    )
    config = _config()
    config["default_section_name"] = "Verse"
    draft = build_draft(reviewed, base, MappingConfig.from_mapping(config))
    assert [section.name for section in draft.chart.sections] == [
        "Verse",
        "Verse",
        "Verse",
    ]
    assert [item.section_ordinal for item in draft.timing_map] == [0, 1, 2]


def test_all_no_chord_review_promotes_without_guitar_decisions() -> None:
    base = _timeline()
    reviewed = root_timeline(base)
    raw = root_timeline(base)
    for segment in tuple(reviewed.segments):
        reviewed = apply_edit(
            reviewed,
            ReviewEdit.create("set_no_chord", {"segment_id": segment.id}),
            base=base,
            raw_root=raw,
        )
    reviewed = apply_edit(
        reviewed,
        ReviewEdit.create("accept_boundaries", {}),
        base=base,
        raw_root=raw,
    )
    reviewed = apply_edit(
        reviewed,
        ReviewEdit.create("finish_review", {}),
        base=base,
        raw_root=raw,
    )
    config = _config()
    config["guitar_decisions"] = []
    draft = build_draft(reviewed, base, MappingConfig.from_mapping(config))
    assert draft.chart.used_chords == ("N.C.",)
    assert all(
        measure.chords == ("N.C.",)
        for section in draft.chart.sections
        for measure in section.bars
    )


def test_all_no_chord_service_approval_and_exports(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path, all_no_chord=True, add_section=False)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    config = _config()
    config["guitar_decisions"] = []
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=config,
    )
    approved = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-all-nc-approve",
        config_mapping=config,
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    exports = service.exports(approved["approval"]["approval_id"])
    assert "N.C." in exports["markdown"]
    assert json.loads(exports["json"])["sections"][0]["bars"][0]["chords"] == [
        "N.C."
    ]


def test_revocation_visibility_comes_only_from_authoritative_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(
        tmp_path,
        clock=iter(
            (
                "2026-07-23T12:00:00+00:00",
                "2026-07-23T12:01:00+00:00",
                "2026-07-23T12:02:00+00:00",
            )
        ).__next__,
    )
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    approved = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-revoke-crash-approve",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    original = service.store._append_event

    def interrupt(*_args, **_kwargs):
        raise OSError("injected revoke event interruption")

    monkeypatch.setattr(service.store, "_append_event", interrupt)
    with pytest.raises(OSError):
        service.revoke(
            approved["approval"]["approval_id"],
            expected_token=approved["approval"]["token"],
            idempotency_key="stage4-revoke-crash-first",
            reason="mistake",
        )
    still_active = service.get(approved["approval"]["approval_id"])
    assert still_active["approval"]["status"] == "active"
    assert still_active["approval"]["revocation"] is None

    monkeypatch.setattr(service.store, "_append_event", original)
    revoked = service.revoke(
        approved["approval"]["approval_id"],
        expected_token=approved["approval"]["token"],
        idempotency_key="stage4-revoke-crash-second",
        reason="withdrawn",
    )
    assert revoked["approval"]["status"] == "revoked"
    assert revoked["approval"]["revocation"]["reason"] == "withdrawn"


def test_export_requires_intact_persisted_spec_and_config(tmp_path: Path) -> None:
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    approved = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-spec-integrity-approve",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    approval = service.store.load_approval(approved["approval"]["approval_id"])
    spec_path = service.store.specs / f"{approval.spec_id[7:]}.json"
    spec_path.unlink()
    with pytest.raises(PromotionError) as missing:
        service.exports(approval.id)
    assert missing.value.code == "promotion_storage_integrity"


def test_rejected_receipts_hit_structured_project_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(promotion_store_module, "_MAX_RECEIPTS", 2)
    ready, _ = _ready(tmp_path)
    service = PromotionService(tmp_path, clock=lambda: "2026-07-23T12:00:00+00:00")
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-receipt-limit-one",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    with pytest.raises(PromotionError) as active:
        service.approve(
            ready["session"]["session_id"],
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            idempotency_key="stage4-receipt-limit-two",
            config_mapping=_config(),
            expected_spec_id=preview["spec_id"],
            expected_result_id=preview["result_id"],
            expected_issue_digest=preview["issue_digest"],
            acknowledged_issue_ids=_material_ids(preview),
        )
    assert active.value.code == "active_approval_exists"
    before = len(list(service.store.receipts.glob("*.json")))
    with pytest.raises(PromotionError) as limited:
        service.approve(
            ready["session"]["session_id"],
            ready["head"]["revision_id"],
            expected_head_token=ready["head"]["token"],
            idempotency_key="stage4-receipt-limit-three",
            config_mapping=_config(),
            expected_spec_id=preview["spec_id"],
            expected_result_id=preview["result_id"],
            expected_issue_digest=preview["issue_digest"],
            acknowledged_issue_ids=_material_ids(preview),
        )
    assert limited.value.code == "promotion_limit"
    assert len(list(service.store.receipts.glob("*.json"))) == before


def test_backward_clock_is_clamped_across_revoke_and_reapproval(
    tmp_path: Path,
) -> None:
    ready, _ = _ready(tmp_path)
    times = iter(
        (
            "2026-07-23T12:00:00+00:00",
            "2026-07-23T11:00:00+00:00",
            "2026-07-23T10:00:00+00:00",
        )
    )
    service = PromotionService(tmp_path, clock=times.__next__)
    preview = service.preview(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        config_mapping=_config(),
    )
    first = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-backward-first",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    revoked = service.revoke(
        first["approval"]["approval_id"],
        expected_token=first["approval"]["token"],
        idempotency_key="stage4-backward-revoke",
        reason="superseded",
    )
    second = service.approve(
        ready["session"]["session_id"],
        ready["head"]["revision_id"],
        expected_head_token=ready["head"]["token"],
        idempotency_key="stage4-backward-second",
        config_mapping=_config(),
        expected_spec_id=preview["spec_id"],
        expected_result_id=preview["result_id"],
        expected_issue_digest=preview["issue_digest"],
        acknowledged_issue_ids=_material_ids(preview),
    )
    assert first["approval"]["approved_at"] == "2026-07-23T12:00:00+00:00"
    assert (
        revoked["approval"]["revocation"]["revoked_at"]
        == "2026-07-23T12:00:00+00:00"
    )
    assert second["approval"]["approved_at"] == "2026-07-23T12:00:00+00:00"
