from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from chordatlas.media import FrameRange, MediaImportError, ProjectMediaStore
from chordatlas.practice.models import (
    MAX_RATE_MILLI,
    MIN_RATE_MILLI,
    PracticeAttempt,
    PracticeError,
    PracticeSession,
    PracticeTarget,
    content_digest,
)
from chordatlas.practice.store import PracticeStore
from chordatlas.promotion import PromotionError, PromotionService

PRACTICE_RESET_CONFIRMATION = "CLEAR ALL PRACTICE HISTORY"


class PracticeService:
    """Trusted Stage 6 coordinator for private, approval-bound practice state."""

    def __init__(
        self,
        project_root: Path,
        *,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ) -> None:
        self.media = ProjectMediaStore.initialize(project_root)
        self.promotion = PromotionService(project_root)
        self.store = PracticeStore.initialize(project_root)
        self._clock = clock

    def create(self, approval_id: str, *, idempotency_key: str) -> dict[str, Any]:
        with self.store.lifecycle():
            return self._create_locked(approval_id, idempotency_key=idempotency_key)

    def _create_locked(self, approval_id: str, *, idempotency_key: str) -> dict[str, Any]:
        try:
            basis = self.promotion.load_practice_basis(
                approval_id,
                require_active=True,
            )
        except (PromotionError, MediaImportError) as error:
            raise _upstream_error(error) from None
        targets = _targets_for_basis(basis)
        identity_fingerprint = content_digest(
            {
                "action": "create_practice_session",
                "approval_id": basis.approval_id,
                "promotion_result_id": basis.promotion_result_id,
                "source_id": basis.source_id,
                "asset_id": basis.asset_id,
                "targets": [item.to_record_mapping() for item in targets],
            }
        )
        self.store.validate_creation_key(
            idempotency_key=idempotency_key,
            fingerprint=identity_fingerprint,
        )
        proposed_at = self._clock()
        session = PracticeSession.create(
            source_id=basis.source_id,
            asset_id=basis.asset_id,
            approval_id=basis.approval_id,
            promotion_result_id=basis.promotion_result_id,
            review_session_id=basis.review_session_id,
            review_revision_id=basis.review_revision_id,
            timebase=basis.timebase,
            analyzed_range=basis.analyzed_range,
            meter_numerator=basis.meter_numerator,
            beat_unit=basis.beat_unit,
            targets=targets,
            created_at=proposed_at,
        )
        try:
            self.store.current(session.id)
        except PracticeError as error:
            if error.code != "practice_unavailable":
                raise
        else:
            # The content-addressed session is already complete. Avoid consuming
            # another idempotency receipt for an equivalent Prepare action.
            return self.get(session.id)

        created_at = self.store.creation_time(
            idempotency_key=idempotency_key,
            fingerprint=identity_fingerprint,
            recorded_at=proposed_at,
        )
        session = PracticeSession.create(
            source_id=basis.source_id,
            asset_id=basis.asset_id,
            approval_id=basis.approval_id,
            promotion_result_id=basis.promotion_result_id,
            review_session_id=basis.review_session_id,
            review_revision_id=basis.review_revision_id,
            timebase=basis.timebase,
            analyzed_range=basis.analyzed_range,
            meter_numerator=basis.meter_numerator,
            beat_unit=basis.beat_unit,
            targets=targets,
            created_at=created_at,
        )
        full = next(item for item in targets if item.kind == "full")
        initial = PracticeAttempt.create(
            session_id=session.id,
            parent_attempt_id=None,
            selection_kind="target",
            target_id=full.id,
            frame_range=full.frame_range,
            position_frame=full.frame_range.start_frame,
            loop_enabled=False,
            rate_milli=1_000,
            count_in_beats=0,
            count_in_beat_frames_num=0,
            count_in_beat_frames_den=1,
            recorded_at=created_at,
        )
        self.store.publish_initial(session, initial)
        return self.get(session.id)

    def list(self, *, source_id: str) -> list[dict[str, Any]]:
        with self.store.lifecycle():
            return [
                self._view(item.id)
                for item in self.store.list_sessions(source_id=source_id)
            ]

    def get(self, session_id: str) -> dict[str, Any]:
        with self.store.lifecycle():
            return self._view(session_id)

    def update(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        state_mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.store.lifecycle():
            return self._update_locked(
                session_id,
                expected_token=expected_token,
                idempotency_key=idempotency_key,
                state_mapping=state_mapping,
            )

    def _update_locked(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        state_mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        self._require_available(session)
        state = _normalize_state(session, state_mapping)
        fingerprint = content_digest(
            {
                "action": "save_practice_attempt",
                "practice_session_id": session.id,
                "expected_token": expected_token,
                "state": state,
            }
        )

        def build(parent_attempt_id: str, recorded_at: str) -> PracticeAttempt:
            return PracticeAttempt.create(
                session_id=session.id,
                parent_attempt_id=parent_attempt_id,
                selection_kind=str(state["selection_kind"]),
                target_id=state["target_id"],
                frame_range=FrameRange(
                    int(state["range"]["start_frame"]),
                    int(state["range"]["end_frame"]),
                ),
                position_frame=int(state["position_frame"]),
                loop_enabled=bool(state["loop_enabled"]),
                rate_milli=int(state["rate_milli"]),
                count_in_beats=int(state["count_in_beats"]),
                count_in_beat_frames_num=int(state["count_in_beat_frames_num"]),
                count_in_beat_frames_den=int(state["count_in_beat_frames_den"]),
                recorded_at=recorded_at,
            )

        self.store.record_attempt(
            session.id,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            recorded_at=self._clock(),
            build=build,
        )
        return self.get(session.id)

    def usage(self) -> dict[str, Any]:
        return self.store.inventory()

    def reset(
        self,
        *,
        expected_token: str,
        idempotency_key: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != PRACTICE_RESET_CONFIRMATION:
            raise PracticeError(
                "practice_confirmation_required",
                "Type the exact practice-history reset confirmation.",
            )
        if (
            not isinstance(expected_token, str)
            or len(expected_token) != 64
            or any(character not in "0123456789abcdef" for character in expected_token)
        ):
            raise PracticeError(
                "precondition_required",
                "Practice reset requires the current strong usage token.",
            )
        return self.store.reset(
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            confirmation=confirmation,
            recorded_at=self._clock(),
        )

    def _view(self, session_id: str) -> dict[str, Any]:
        session, head, attempt = self.store.current(session_id)
        availability = self._availability(session)
        count_in = {
            "mode": "off" if attempt.count_in_beats == 0 else "one_approved_bar",
            "beats": attempt.count_in_beats,
            "beat_duration_ms": (
                None
                if attempt.count_in_beats == 0
                else _count_in_beat_duration_ms(
                    numerator=attempt.count_in_beat_frames_num,
                    denominator=attempt.count_in_beat_frames_den,
                    sample_rate=session.timebase.sample_rate,
                    rate_milli=attempt.rate_milli,
                )
            ),
            "timing_authority": (
                "disabled"
                if attempt.count_in_beats == 0
                else "approved_measure_grid"
            ),
        }
        return {
            "practice_session": {
                "practice_session_id": session.id,
                "approval_id": session.approval_id,
                "created_at": session.created_at,
                "timebase": session.timebase.to_mapping(),
                "analyzed_range": session.analyzed_range.to_mapping(),
                "meter": {
                    "numerator": session.meter_numerator,
                    "beat_unit": session.beat_unit,
                },
                "targets": [item.to_private_mapping() for item in session.targets],
            },
            "head": {
                "token": head.token,
                "generation": head.generation,
                "updated_at": head.updated_at,
            },
            "attempt": {
                "attempt_id": attempt.id,
                "selection_kind": attempt.selection_kind,
                "target_id": attempt.target_id,
                "range": attempt.frame_range.to_mapping(),
                "position_frame": attempt.position_frame,
                "loop_enabled": attempt.loop_enabled,
                "rate_milli": attempt.rate_milli,
                "rate_percent": attempt.rate_milli / 10,
                "pitch_behavior": (
                    "Browser playback-rate change; pitch may change. "
                    "Pitch preservation is not provided."
                ),
                "count_in": count_in,
                "recorded_at": attempt.recorded_at,
                "restored_playing": False,
            },
            "availability": availability,
            "privacy": {
                "project_local": True,
                "external_telemetry": False,
                "excluded_from_songchart_and_exports": True,
            },
        }

    def _availability(self, session: PracticeSession) -> dict[str, Any]:
        try:
            basis = self.promotion.load_practice_basis(
                session.approval_id,
                require_active=False,
            )
        except MediaImportError:
            return {
                "status": "media_unavailable",
                "message": "The authorized local media is unavailable or failed validation.",
                "can_update": False,
            }
        except PromotionError:
            return {
                "status": "unavailable",
                "message": "The approved private timing basis is unavailable.",
                "can_update": False,
            }
        if not basis.approval_active:
            return {
                "status": "approval_revoked",
                "message": "Approval was revoked. Saved practice history remains private.",
                "can_update": False,
            }
        if not basis.review_current:
            return {
                "status": "review_changed",
                "message": "The review changed. Approve it again before practicing.",
                "can_update": False,
            }
        if not _basis_matches_session(basis, session):
            return {
                "status": "basis_changed",
                "message": "The approved timing basis no longer matches this practice session.",
                "can_update": False,
            }
        try:
            self.media.audio_path_for_source(session.source_id)
        except MediaImportError:
            return {
                "status": "media_unavailable",
                "message": "The authorized local media is unavailable or failed validation.",
                "can_update": False,
            }
        return {
            "status": "ready",
            "message": "Approved local media and exact frame mapping are ready.",
            "can_update": True,
        }

    def _require_available(self, session: PracticeSession) -> None:
        availability = self._availability(session)
        if availability["status"] != "ready":
            raise PracticeError(
                str(availability["status"]),
                str(availability["message"]),
                retryable=availability["status"] in {"media_unavailable"},
            )


def _targets_for_basis(basis) -> tuple[PracticeTarget, ...]:
    measures = tuple(basis.timing_map)
    targets: list[PracticeTarget] = [
        PracticeTarget.create(
            kind="full",
            label="Full approved chart",
            occurrence=1,
            start_measure=measures[0].measure_number,
            end_measure=measures[-1].measure_number,
            frame_range=FrameRange(
                measures[0].start_frame,
                measures[-1].end_frame,
            ),
        )
    ]
    name_occurrences: dict[str, int] = {}
    index = 0
    while index < len(measures):
        first = measures[index]
        section = (first.section_ordinal, first.section_name)
        end_index = index + 1
        while end_index < len(measures) and (
            measures[end_index].section_ordinal,
            measures[end_index].section_name,
        ) == section:
            end_index += 1
        name_occurrences[first.section_name] = name_occurrences.get(first.section_name, 0) + 1
        last = measures[end_index - 1]
        targets.append(
            PracticeTarget.create(
                kind="section",
                label=first.section_name,
                occurrence=name_occurrences[first.section_name],
                start_measure=first.measure_number,
                end_measure=last.measure_number,
                frame_range=FrameRange(first.start_frame, last.end_frame),
            )
        )
        index = end_index
    targets.extend(
        PracticeTarget.create(
            kind="measure",
            label=f"Measure {measure.measure_number} · {measure.section_name}",
            occurrence=measure.measure_number,
            start_measure=measure.measure_number,
            end_measure=measure.measure_number,
            frame_range=FrameRange(measure.start_frame, measure.end_frame),
        )
        for measure in measures
    )
    return tuple(targets)


def _normalize_state(
    session: PracticeSession,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "target_id",
        "custom_range",
        "position_frame",
        "loop_enabled",
        "rate_milli",
        "count_in",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PracticeError(
            "invalid_practice_state",
            "Practice state fields are invalid.",
        )
    target_id = value["target_id"]
    custom = value["custom_range"]
    if target_id is not None:
        if not isinstance(target_id, str) or custom is not None:
            raise PracticeError(
                "invalid_practice_target",
                "Choose one approved target or one custom frame range.",
            )
        target = next((item for item in session.targets if item.id == target_id), None)
        if target is None:
            raise PracticeError(
                "invalid_practice_target",
                "The selected approved practice target is unavailable.",
            )
        selection_kind = "target"
        frame_range = target.frame_range
    else:
        if (
            not isinstance(custom, Mapping)
            or set(custom) != {"start_frame", "end_frame"}
        ):
            raise PracticeError(
                "invalid_practice_target",
                "Enter one complete custom integer-frame range.",
            )
        start = _strict_int(custom["start_frame"])
        end = _strict_int(custom["end_frame"])
        try:
            frame_range = FrameRange(start, end)
            session.timebase.validate_range(frame_range)
        except ValueError:
            raise PracticeError(
                "invalid_practice_range",
                "The custom practice range is outside the authorized media.",
            ) from None
        if (
            start < session.analyzed_range.start_frame
            or end > session.analyzed_range.end_frame
        ):
            raise PracticeError(
                "invalid_practice_range",
                "Custom practice must stay inside the approved chart range.",
            )
        selection_kind = "custom"
    position = _strict_int(value["position_frame"])
    if not frame_range.start_frame <= position < frame_range.end_frame:
        raise PracticeError(
            "invalid_practice_position",
            "The saved practice position must be inside the selected range.",
        )
    if type(value["loop_enabled"]) is not bool:
        raise PracticeError(
            "invalid_practice_state",
            "Loop state must be explicitly enabled or disabled.",
        )
    rate_milli = _strict_int(value["rate_milli"])
    if not MIN_RATE_MILLI <= rate_milli <= MAX_RATE_MILLI:
        raise PracticeError(
            "invalid_practice_rate",
            "Practice speed must be between 50% and 125%.",
        )
    count_in = value["count_in"]
    if count_in not in {"off", "one_approved_bar"}:
        raise PracticeError(
            "invalid_count_in",
            "Count-in must be off or one approved bar.",
        )
    if count_in == "off":
        count_in_beats = 0
        beat_num = 0
        beat_den = 1
    else:
        measure = _measure_for_frame(session, frame_range.start_frame)
        count_in_beats = session.meter_numerator
        beat_num = measure.frame_range.length_frames
        beat_den = session.meter_numerator
    return {
        "selection_kind": selection_kind,
        "target_id": target_id,
        "range": frame_range.to_mapping(),
        "position_frame": position,
        "loop_enabled": value["loop_enabled"],
        "rate_milli": rate_milli,
        "count_in_beats": count_in_beats,
        "count_in_beat_frames_num": beat_num,
        "count_in_beat_frames_den": beat_den,
    }


def _measure_for_frame(session: PracticeSession, frame: int) -> PracticeTarget:
    for target in session.targets:
        if (
            target.kind == "measure"
            and target.frame_range.start_frame <= frame < target.frame_range.end_frame
        ):
            return target
    raise PracticeError(
        "invalid_count_in",
        "The selected range does not begin inside an approved measure.",
    )


def _basis_matches_session(basis, session: PracticeSession) -> bool:
    return (
        basis.approval_id == session.approval_id
        and basis.promotion_result_id == session.promotion_result_id
        and basis.review_session_id == session.review_session_id
        and basis.review_revision_id == session.review_revision_id
        and basis.source_id == session.source_id
        and basis.asset_id == session.asset_id
        and basis.timebase == session.timebase
        and basis.analyzed_range == session.analyzed_range
        and basis.meter_numerator == session.meter_numerator
        and basis.beat_unit == session.beat_unit
    )


def _count_in_beat_duration_ms(
    *,
    numerator: int,
    denominator: int,
    sample_rate: int,
    rate_milli: int,
) -> int:
    divisor = denominator * sample_rate * rate_milli
    return max((numerator * 1_000_000 + divisor // 2) // divisor, 1)


def _strict_int(value: Any) -> int:
    if type(value) is not int:
        raise PracticeError(
            "invalid_practice_state",
            "Practice frame and speed fields must be integers.",
        )
    return value


def _upstream_error(error) -> PracticeError:
    code = getattr(error, "code", "practice_unavailable")
    if code == "approval_revoked":
        return PracticeError(
            "approval_revoked",
            "Approval was revoked. Saved practice history remains private.",
        )
    if code in {"review_changed", "review_conflict"}:
        return PracticeError(
            "review_changed",
            "The review changed. Approve it again before practicing.",
        )
    if code in {"unknown_media", "media_missing"}:
        return PracticeError(
            "media_unavailable",
            "The authorized local media is unavailable.",
            retryable=True,
        )
    return PracticeError(
        "practice_unavailable",
        "The approved private practice basis is unavailable.",
    )
