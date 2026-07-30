from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from chordatlas.media import FrameRange, Timebase

_SESSION_RE = re.compile(r"^practice_[0-9a-f]{64}$")
_ATTEMPT_RE = re.compile(r"^attempt_[0-9a-f]{64}$")
_TARGET_RE = re.compile(r"^target_[0-9a-f]{64}$")
_SOURCE_RE = re.compile(r"^src_[0-9a-f]{32}$")
_APPROVAL_RE = re.compile(r"^apr_[0-9a-f]{64}$")
_RESULT_RE = re.compile(r"^pro_[0-9a-f]{64}$")
_REVIEW_RE = re.compile(r"^review_[0-9a-f]{32}$")
_REVISION_RE = re.compile(r"^rrv_[0-9a-f]{64}$")
_ASSET_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

MIN_RATE_MILLI = 500
MAX_RATE_MILLI = 1_250
MAX_TARGETS = 2_048
MAX_LABEL_BYTES = 192


class PracticeError(ValueError):
    """A stable, public-safe private-practice failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable

    def to_mapping(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.public_message,
                "retryable": self.retryable,
            }
        }


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class PracticeTarget:
    id: str
    kind: str
    label: str
    occurrence: int
    start_measure: int
    end_measure: int
    frame_range: FrameRange

    def __post_init__(self) -> None:
        if (
            _TARGET_RE.fullmatch(self.id) is None
            or self.kind not in {"full", "section", "measure"}
            or self.occurrence <= 0
            or self.start_measure <= 0
            or self.end_measure < self.start_measure
        ):
            raise ValueError("practice target is invalid")
        _bounded_text(self.label, "practice target label")
        if self.id != f"target_{content_digest(self.identity_mapping())}":
            raise ValueError("practice target id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        label: str,
        occurrence: int,
        start_measure: int,
        end_measure: int,
        frame_range: FrameRange,
    ) -> PracticeTarget:
        identity = {
            "practice_target_schema_version": "1.0.0-draft",
            "kind": kind,
            "label": label,
            "occurrence": occurrence,
            "start_measure": start_measure,
            "end_measure": end_measure,
            "range": frame_range.to_mapping(),
        }
        return cls(
            id=f"target_{content_digest(identity)}",
            kind=kind,
            label=label,
            occurrence=occurrence,
            start_measure=start_measure,
            end_measure=end_measure,
            frame_range=frame_range,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "practice_target_schema_version": "1.0.0-draft",
            "kind": self.kind,
            "label": self.label,
            "occurrence": self.occurrence,
            "start_measure": self.start_measure,
            "end_measure": self.end_measure,
            "range": self.frame_range.to_mapping(),
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    def to_private_mapping(self) -> dict[str, Any]:
        return {
            "target_id": self.id,
            "kind": self.kind,
            "label": self.label,
            "occurrence": self.occurrence,
            "start_measure": self.start_measure,
            "end_measure": self.end_measure,
            "range": self.frame_range.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PracticeTarget:
        expected = {
            "id",
            "practice_target_schema_version",
            "kind",
            "label",
            "occurrence",
            "start_measure",
            "end_measure",
            "range",
        }
        frame_range = value.get("range")
        if (
            set(value) != expected
            or value.get("practice_target_schema_version") != "1.0.0-draft"
            or not isinstance(frame_range, Mapping)
            or set(frame_range) != {"start_frame", "end_frame"}
        ):
            raise ValueError("practice target record is invalid")
        return cls(
            id=_string(value["id"]),
            kind=_string(value["kind"]),
            label=_string(value["label"]),
            occurrence=_integer(value["occurrence"]),
            start_measure=_integer(value["start_measure"]),
            end_measure=_integer(value["end_measure"]),
            frame_range=FrameRange(
                _integer(frame_range["start_frame"]),
                _integer(frame_range["end_frame"]),
            ),
        )


@dataclass(frozen=True)
class PracticeSession:
    id: str
    source_id: str
    asset_id: str
    approval_id: str
    promotion_result_id: str
    review_session_id: str
    review_revision_id: str
    timebase: Timebase
    analyzed_range: FrameRange
    meter_numerator: int
    beat_unit: int
    targets: tuple[PracticeTarget, ...]
    created_at: str

    def __post_init__(self) -> None:
        if (
            _SESSION_RE.fullmatch(self.id) is None
            or _SOURCE_RE.fullmatch(self.source_id) is None
            or _ASSET_RE.fullmatch(self.asset_id) is None
            or _APPROVAL_RE.fullmatch(self.approval_id) is None
            or _RESULT_RE.fullmatch(self.promotion_result_id) is None
            or _REVIEW_RE.fullmatch(self.review_session_id) is None
            or _REVISION_RE.fullmatch(self.review_revision_id) is None
            or self.meter_numerator <= 0
            or self.meter_numerator > 32
            or self.beat_unit not in {1, 2, 4, 8, 16, 32}
            or not 1 <= len(self.targets) <= MAX_TARGETS
        ):
            raise ValueError("practice session is invalid")
        _timestamp(self.created_at)
        self.timebase.validate_range(self.analyzed_range)
        if len({item.id for item in self.targets}) != len(self.targets):
            raise ValueError("practice target ids must be unique")
        for target in self.targets:
            self.timebase.validate_range(target.frame_range)
            if (
                target.frame_range.start_frame < self.analyzed_range.start_frame
                or target.frame_range.end_frame > self.analyzed_range.end_frame
            ):
                raise ValueError("practice target is outside the approved range")
        if self.id != f"practice_{content_digest(self.identity_mapping())}":
            raise ValueError("practice session id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        asset_id: str,
        approval_id: str,
        promotion_result_id: str,
        review_session_id: str,
        review_revision_id: str,
        timebase: Timebase,
        analyzed_range: FrameRange,
        meter_numerator: int,
        beat_unit: int,
        targets: tuple[PracticeTarget, ...],
        created_at: str,
    ) -> PracticeSession:
        identity = _session_identity(
            source_id=source_id,
            asset_id=asset_id,
            approval_id=approval_id,
            promotion_result_id=promotion_result_id,
            review_session_id=review_session_id,
            review_revision_id=review_revision_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            meter_numerator=meter_numerator,
            beat_unit=beat_unit,
            targets=targets,
        )
        return cls(
            id=f"practice_{content_digest(identity)}",
            source_id=source_id,
            asset_id=asset_id,
            approval_id=approval_id,
            promotion_result_id=promotion_result_id,
            review_session_id=review_session_id,
            review_revision_id=review_revision_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            meter_numerator=meter_numerator,
            beat_unit=beat_unit,
            targets=targets,
            created_at=created_at,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return _session_identity(
            source_id=self.source_id,
            asset_id=self.asset_id,
            approval_id=self.approval_id,
            promotion_result_id=self.promotion_result_id,
            review_session_id=self.review_session_id,
            review_revision_id=self.review_revision_id,
            timebase=self.timebase,
            analyzed_range=self.analyzed_range,
            meter_numerator=self.meter_numerator,
            beat_unit=self.beat_unit,
            targets=self.targets,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            **self.identity_mapping(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PracticeSession:
        expected = {
            "id",
            "practice_session_schema_version",
            "source_id",
            "asset_id",
            "approval_id",
            "promotion_result_id",
            "review_session_id",
            "review_revision_id",
            "timebase",
            "analyzed_range",
            "meter_numerator",
            "beat_unit",
            "targets",
            "created_at",
        }
        timebase = value.get("timebase")
        analyzed = value.get("analyzed_range")
        targets = value.get("targets")
        if (
            set(value) != expected
            or value.get("practice_session_schema_version") != "1.0.0-draft"
            or not isinstance(timebase, Mapping)
            or set(timebase) != {"unit", "sample_rate", "duration_frames"}
            or timebase.get("unit") != "sample_frame"
            or not isinstance(analyzed, Mapping)
            or set(analyzed) != {"start_frame", "end_frame"}
            or not isinstance(targets, list)
            or any(not isinstance(item, Mapping) for item in targets)
        ):
            raise ValueError("practice session record is invalid")
        return cls(
            id=_string(value["id"]),
            source_id=_string(value["source_id"]),
            asset_id=_string(value["asset_id"]),
            approval_id=_string(value["approval_id"]),
            promotion_result_id=_string(value["promotion_result_id"]),
            review_session_id=_string(value["review_session_id"]),
            review_revision_id=_string(value["review_revision_id"]),
            timebase=Timebase(
                _integer(timebase["sample_rate"]),
                _integer(timebase["duration_frames"]),
            ),
            analyzed_range=FrameRange(
                _integer(analyzed["start_frame"]),
                _integer(analyzed["end_frame"]),
            ),
            meter_numerator=_integer(value["meter_numerator"]),
            beat_unit=_integer(value["beat_unit"]),
            targets=tuple(PracticeTarget.from_mapping(item) for item in targets),
            created_at=_string(value["created_at"]),
        )


@dataclass(frozen=True)
class PracticeAttempt:
    id: str
    session_id: str
    parent_attempt_id: str | None
    selection_kind: str
    target_id: str | None
    frame_range: FrameRange
    position_frame: int
    loop_enabled: bool
    rate_milli: int
    count_in_beats: int
    count_in_beat_frames_num: int
    count_in_beat_frames_den: int
    recorded_at: str

    def __post_init__(self) -> None:
        if (
            _ATTEMPT_RE.fullmatch(self.id) is None
            or _SESSION_RE.fullmatch(self.session_id) is None
            or (
                self.parent_attempt_id is not None
                and _ATTEMPT_RE.fullmatch(self.parent_attempt_id) is None
            )
            or self.selection_kind not in {"target", "custom"}
            or (
                self.selection_kind == "target"
                and (self.target_id is None or _TARGET_RE.fullmatch(self.target_id) is None)
            )
            or (self.selection_kind == "custom" and self.target_id is not None)
            or type(self.position_frame) is not int
            or not self.frame_range.start_frame
            <= self.position_frame
            < self.frame_range.end_frame
            or type(self.loop_enabled) is not bool
            or not MIN_RATE_MILLI <= self.rate_milli <= MAX_RATE_MILLI
            or not 0 <= self.count_in_beats <= 32
            or self.count_in_beat_frames_num < 0
            or self.count_in_beat_frames_den <= 0
        ):
            raise ValueError("practice attempt is invalid")
        if self.count_in_beats == 0 and self.count_in_beat_frames_num != 0:
            raise ValueError("disabled count-in must not retain timing")
        if self.count_in_beats > 0 and self.count_in_beat_frames_num <= 0:
            raise ValueError("enabled count-in requires a positive beat span")
        _timestamp(self.recorded_at)
        if self.id != f"attempt_{content_digest(self.identity_mapping())}":
            raise ValueError("practice attempt id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        parent_attempt_id: str | None,
        selection_kind: str,
        target_id: str | None,
        frame_range: FrameRange,
        position_frame: int,
        loop_enabled: bool,
        rate_milli: int,
        count_in_beats: int,
        count_in_beat_frames_num: int,
        count_in_beat_frames_den: int,
        recorded_at: str,
    ) -> PracticeAttempt:
        identity = _attempt_identity(
            session_id=session_id,
            parent_attempt_id=parent_attempt_id,
            selection_kind=selection_kind,
            target_id=target_id,
            frame_range=frame_range,
            position_frame=position_frame,
            loop_enabled=loop_enabled,
            rate_milli=rate_milli,
            count_in_beats=count_in_beats,
            count_in_beat_frames_num=count_in_beat_frames_num,
            count_in_beat_frames_den=count_in_beat_frames_den,
            recorded_at=recorded_at,
        )
        return cls(
            id=f"attempt_{content_digest(identity)}",
            session_id=session_id,
            parent_attempt_id=parent_attempt_id,
            selection_kind=selection_kind,
            target_id=target_id,
            frame_range=frame_range,
            position_frame=position_frame,
            loop_enabled=loop_enabled,
            rate_milli=rate_milli,
            count_in_beats=count_in_beats,
            count_in_beat_frames_num=count_in_beat_frames_num,
            count_in_beat_frames_den=count_in_beat_frames_den,
            recorded_at=recorded_at,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return _attempt_identity(
            session_id=self.session_id,
            parent_attempt_id=self.parent_attempt_id,
            selection_kind=self.selection_kind,
            target_id=self.target_id,
            frame_range=self.frame_range,
            position_frame=self.position_frame,
            loop_enabled=self.loop_enabled,
            rate_milli=self.rate_milli,
            count_in_beats=self.count_in_beats,
            count_in_beat_frames_num=self.count_in_beat_frames_num,
            count_in_beat_frames_den=self.count_in_beat_frames_den,
            recorded_at=self.recorded_at,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PracticeAttempt:
        expected = {
            "id",
            "practice_attempt_schema_version",
            "session_id",
            "parent_attempt_id",
            "selection_kind",
            "target_id",
            "range",
            "position_frame",
            "loop_enabled",
            "rate_milli",
            "count_in_beats",
            "count_in_beat_frames_num",
            "count_in_beat_frames_den",
            "recorded_at",
        }
        frame_range = value.get("range")
        if (
            set(value) != expected
            or value.get("practice_attempt_schema_version") != "1.0.0-draft"
            or not isinstance(frame_range, Mapping)
            or set(frame_range) != {"start_frame", "end_frame"}
        ):
            raise ValueError("practice attempt record is invalid")
        parent = value["parent_attempt_id"]
        target = value["target_id"]
        if parent is not None and not isinstance(parent, str):
            raise ValueError("practice parent attempt is invalid")
        if target is not None and not isinstance(target, str):
            raise ValueError("practice target reference is invalid")
        return cls(
            id=_string(value["id"]),
            session_id=_string(value["session_id"]),
            parent_attempt_id=parent,
            selection_kind=_string(value["selection_kind"]),
            target_id=target,
            frame_range=FrameRange(
                _integer(frame_range["start_frame"]),
                _integer(frame_range["end_frame"]),
            ),
            position_frame=_integer(value["position_frame"]),
            loop_enabled=_boolean(value["loop_enabled"]),
            rate_milli=_integer(value["rate_milli"]),
            count_in_beats=_integer(value["count_in_beats"]),
            count_in_beat_frames_num=_integer(value["count_in_beat_frames_num"]),
            count_in_beat_frames_den=_integer(value["count_in_beat_frames_den"]),
            recorded_at=_string(value["recorded_at"]),
        )


@dataclass(frozen=True)
class PracticeHead:
    session_id: str
    generation: int
    attempt_id: str
    updated_at: str

    def __post_init__(self) -> None:
        if (
            _SESSION_RE.fullmatch(self.session_id) is None
            or self.generation < 0
            or _ATTEMPT_RE.fullmatch(self.attempt_id) is None
        ):
            raise ValueError("practice head is invalid")
        _timestamp(self.updated_at)

    @property
    def token(self) -> str:
        return content_digest(self.to_record_mapping())

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "practice_head_schema_version": "1.0.0-draft",
            "session_id": self.session_id,
            "generation": self.generation,
            "attempt_id": self.attempt_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PracticeHead:
        if set(value) != {
            "practice_head_schema_version",
            "session_id",
            "generation",
            "attempt_id",
            "updated_at",
        } or value.get("practice_head_schema_version") != "1.0.0-draft":
            raise ValueError("practice head record is invalid")
        return cls(
            session_id=_string(value["session_id"]),
            generation=_integer(value["generation"]),
            attempt_id=_string(value["attempt_id"]),
            updated_at=_string(value["updated_at"]),
        )


def _session_identity(
    *,
    source_id: str,
    asset_id: str,
    approval_id: str,
    promotion_result_id: str,
    review_session_id: str,
    review_revision_id: str,
    timebase: Timebase,
    analyzed_range: FrameRange,
    meter_numerator: int,
    beat_unit: int,
    targets: tuple[PracticeTarget, ...],
) -> dict[str, Any]:
    return {
        "practice_session_schema_version": "1.0.0-draft",
        "source_id": source_id,
        "asset_id": asset_id,
        "approval_id": approval_id,
        "promotion_result_id": promotion_result_id,
        "review_session_id": review_session_id,
        "review_revision_id": review_revision_id,
        "timebase": timebase.to_mapping(),
        "analyzed_range": analyzed_range.to_mapping(),
        "meter_numerator": meter_numerator,
        "beat_unit": beat_unit,
        "targets": [item.to_record_mapping() for item in targets],
    }


def _attempt_identity(
    *,
    session_id: str,
    parent_attempt_id: str | None,
    selection_kind: str,
    target_id: str | None,
    frame_range: FrameRange,
    position_frame: int,
    loop_enabled: bool,
    rate_milli: int,
    count_in_beats: int,
    count_in_beat_frames_num: int,
    count_in_beat_frames_den: int,
    recorded_at: str,
) -> dict[str, Any]:
    return {
        "practice_attempt_schema_version": "1.0.0-draft",
        "session_id": session_id,
        "parent_attempt_id": parent_attempt_id,
        "selection_kind": selection_kind,
        "target_id": target_id,
        "range": frame_range.to_mapping(),
        "position_frame": position_frame,
        "loop_enabled": loop_enabled,
        "rate_milli": rate_milli,
        "count_in_beats": count_in_beats,
        "count_in_beat_frames_num": count_in_beat_frames_num,
        "count_in_beat_frames_den": count_in_beat_frames_den,
        "recorded_at": recorded_at,
    }


def _bounded_text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > MAX_LABEL_BYTES
        or any(ord(character) < 32 and character not in "\t" for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("integer field is invalid")
    return value


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("boolean field is invalid")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("text field is invalid")
    return value


def _timestamp(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp is invalid")
