from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from chordatlas.models import SongChart
from chordatlas.review.models import canonical_json, content_digest

_APPROVAL_RE = re.compile(r"^apr_[0-9a-f]{64}$")
_RESULT_RE = re.compile(r"^pro_[0-9a-f]{64}$")
_REVOCATION_RE = re.compile(r"^rev_[0-9a-f]{64}$")
_RECEIPT_RE = re.compile(r"^rcp_[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^rrv_[0-9a-f]{64}$")
_TIMELINE_RE = re.compile(r"^rtl_[0-9a-f]{64}$")
_SESSION_RE = re.compile(r"^review_[0-9a-f]{32}$")
_ISSUE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")

MAPPING_VERSION = "songchart-explicit-grid-v1"
MAX_TEXT = 256
MAX_GUITAR_DECISIONS = 256
MAX_ACKNOWLEDGEMENTS = 2_000


class PromotionError(ValueError):
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


@dataclass(frozen=True)
class GuitarDecision:
    chord: str
    inversion_reviewed: bool
    voicing: str
    playability: str
    note: str | None = None

    def __post_init__(self) -> None:
        _text(self.chord, "chord", limit=96)
        if self.voicing not in {"built_in", "manual_required"}:
            raise PromotionError("invalid_mapping", "Voicing decision is invalid.")
        if self.playability not in {"playable", "needs_adjustment"}:
            raise PromotionError("invalid_mapping", "Playability decision is invalid.")
        if self.note is not None:
            _text(self.note, "guitar note")
        if self.voicing == "manual_required" and not self.note:
            raise PromotionError(
                "invalid_mapping",
                "A manual voicing decision requires a concise public note.",
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> GuitarDecision:
        if set(value) != {
            "chord",
            "inversion_reviewed",
            "voicing",
            "playability",
            "note",
        }:
            raise PromotionError("invalid_mapping", "Guitar decision fields are invalid.")
        if not isinstance(value["inversion_reviewed"], bool):
            raise PromotionError("invalid_mapping", "Inversion review must be explicit.")
        return cls(
            chord=_text(value["chord"], "chord", limit=96),
            inversion_reviewed=value["inversion_reviewed"],
            voicing=_text(value["voicing"], "voicing", limit=32),
            playability=_text(value["playability"], "playability", limit=32),
            note=(
                None
                if value["note"] is None
                else _text(value["note"], "guitar note")
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "chord": self.chord,
            "inversion_reviewed": self.inversion_reviewed,
            "voicing": self.voicing,
            "playability": self.playability,
            "note": self.note,
        }


@dataclass(frozen=True)
class MappingConfig:
    title: str
    artist: str | None
    key: str | None
    tuning: str
    capo: str
    chart_version: str
    meter_numerator: int
    beat_unit: int
    measure_boundaries_frames: tuple[int, ...]
    pickup_policy: str
    default_section_name: str
    notation_mode: str
    diagram_policy: str
    loss_policy: str
    guitar_decisions: tuple[GuitarDecision, ...]
    mapping_version: str = MAPPING_VERSION

    def __post_init__(self) -> None:
        _text(self.title, "title")
        for value, name in (
            (self.artist, "artist"),
            (self.key, "key"),
        ):
            if value is not None:
                _text(value, name)
        _text(self.tuning, "tuning", limit=96)
        _text(self.capo, "capo", limit=32)
        _text(self.chart_version, "chart version", limit=32)
        if self.meter_numerator != 4 or self.beat_unit != 4:
            raise PromotionError(
                "invalid_mapping",
                "The first Stage 4 release supports explicitly confirmed 4/4 only.",
            )
        if (
            len(self.measure_boundaries_frames) < 2
            or len(self.measure_boundaries_frames) > 10_001
            or self.measure_boundaries_frames
            != tuple(sorted(set(self.measure_boundaries_frames)))
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for item in self.measure_boundaries_frames
            )
        ):
            raise PromotionError(
                "invalid_mapping",
                "Measure boundaries must be unique ordered integer frames.",
            )
        if self.pickup_policy not in {"full_coverage_confirmed"}:
            raise PromotionError(
                "invalid_mapping",
                "Pickup and trailing coverage must be explicitly confirmed.",
            )
        _text(self.default_section_name, "default section", limit=96)
        if self.notation_mode != "sounding":
            raise PromotionError(
                "invalid_mapping",
                "Stage 4 exports sounding chord symbols only.",
            )
        if self.diagram_policy != "built_in_standard_only":
            raise PromotionError(
                "invalid_mapping",
                "Stage 4 supports built-in standard-tuning references only.",
            )
        if self.loss_policy != "allow_declared":
            raise PromotionError(
                "invalid_mapping",
                "SongChart timing loss must remain explicitly declared.",
            )
        if self.mapping_version != MAPPING_VERSION:
            raise PromotionError("invalid_mapping", "Mapping version is unsupported.")
        if len(self.guitar_decisions) > MAX_GUITAR_DECISIONS:
            raise PromotionError("invalid_mapping", "Guitar decisions are excessive.")
        labels = tuple(item.chord for item in self.guitar_decisions)
        if labels != tuple(sorted(set(labels))):
            raise PromotionError(
                "invalid_mapping",
                "Guitar decisions must be unique and sorted by chord.",
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MappingConfig:
        expected = {
            "title",
            "artist",
            "key",
            "tuning",
            "capo",
            "chart_version",
            "meter_numerator",
            "beat_unit",
            "measure_boundaries_frames",
            "pickup_policy",
            "default_section_name",
            "notation_mode",
            "diagram_policy",
            "loss_policy",
            "guitar_decisions",
            "mapping_version",
        }
        if (
            set(value) != expected
            or not isinstance(value["guitar_decisions"], list)
            or not isinstance(value["measure_boundaries_frames"], list)
        ):
            raise PromotionError("invalid_mapping", "Promotion mapping fields are invalid.")
        decisions = tuple(
            GuitarDecision.from_mapping(item)
            for item in value["guitar_decisions"]
            if isinstance(item, Mapping)
        )
        if len(decisions) != len(value["guitar_decisions"]):
            raise PromotionError("invalid_mapping", "Guitar decisions must be objects.")
        return cls(
            title=_text(value["title"], "title"),
            artist=None if value["artist"] is None else _text(value["artist"], "artist"),
            key=None if value["key"] is None else _text(value["key"], "key"),
            tuning=_text(value["tuning"], "tuning", limit=96),
            capo=_text(value["capo"], "capo", limit=32),
            chart_version=_text(value["chart_version"], "chart version", limit=32),
            meter_numerator=_integer(value["meter_numerator"]),
            beat_unit=_integer(value["beat_unit"]),
            measure_boundaries_frames=tuple(
                _integer(item) for item in value["measure_boundaries_frames"]
            ),
            pickup_policy=_text(value["pickup_policy"], "pickup policy", limit=32),
            default_section_name=_text(
                value["default_section_name"], "default section", limit=96
            ),
            notation_mode=_text(value["notation_mode"], "notation mode", limit=32),
            diagram_policy=_text(value["diagram_policy"], "diagram policy", limit=48),
            loss_policy=_text(value["loss_policy"], "loss policy", limit=32),
            guitar_decisions=decisions,
            mapping_version=_text(value["mapping_version"], "mapping version", limit=64),
        )

    @property
    def id(self) -> str:
        return f"sha256:{content_digest(self.to_mapping())}"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mapping_version": self.mapping_version,
            "title": self.title,
            "artist": self.artist,
            "key": self.key,
            "tuning": self.tuning,
            "capo": self.capo,
            "chart_version": self.chart_version,
            "meter_numerator": self.meter_numerator,
            "beat_unit": self.beat_unit,
            "measure_boundaries_frames": list(self.measure_boundaries_frames),
            "pickup_policy": self.pickup_policy,
            "default_section_name": self.default_section_name,
            "notation_mode": self.notation_mode,
            "diagram_policy": self.diagram_policy,
            "loss_policy": self.loss_policy,
            "guitar_decisions": [item.to_mapping() for item in self.guitar_decisions],
        }


@dataclass(frozen=True)
class PromotionIssue:
    id: str
    severity: str
    code: str
    message: str
    details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if _ISSUE_RE.fullmatch(self.code) is None:
            raise ValueError("promotion issue code is invalid")
        if self.severity not in {"blocking", "material", "information"}:
            raise ValueError("promotion issue severity is invalid")
        _text(self.message, "issue message", limit=512)
        if self.id != f"issue_{content_digest(self.payload_mapping())[:24]}":
            raise ValueError("promotion issue id does not match its content")

    @classmethod
    def create(
        cls,
        severity: str,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> PromotionIssue:
        payload = {
            "severity": severity,
            "code": code,
            "message": message,
            "details": dict(sorted((details or {}).items())),
        }
        return cls(
            id=f"issue_{content_digest(payload)[:24]}",
            severity=severity,
            code=code,
            message=message,
            details=tuple(payload["details"].items()),
        )

    def payload_mapping(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }

    def to_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.payload_mapping()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PromotionIssue:
        if set(value) != {"id", "severity", "code", "message", "details"}:
            raise ValueError("promotion issue record is invalid")
        details = value["details"]
        if not isinstance(details, Mapping):
            raise ValueError("promotion issue details are invalid")
        return cls(
            id=str(value["id"]),
            severity=str(value["severity"]),
            code=str(value["code"]),
            message=str(value["message"]),
            details=tuple(sorted(details.items())),
        )


@dataclass(frozen=True)
class MeasureProjection:
    measure_number: int
    start_frame: int
    end_frame: int
    section_name: str
    section_ordinal: int
    chord_spans: tuple[tuple[str, int, int], ...]

    def __post_init__(self) -> None:
        if (
            self.measure_number <= 0
            or self.start_frame < 0
            or self.end_frame <= self.start_frame
            or self.section_ordinal < 0
            or not self.chord_spans
        ):
            raise ValueError("measure projection is invalid")
        _text(self.section_name, "section name", limit=96)
        for label, start, end in self.chord_spans:
            _text(label, "projected chord", limit=96)
            if not self.start_frame <= start < end <= self.end_frame:
                raise ValueError("projected chord span is outside its measure")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "measure_number": self.measure_number,
            "range": {
                "start_frame": self.start_frame,
                "end_frame": self.end_frame,
            },
            "section_name": self.section_name,
            "section_ordinal": self.section_ordinal,
            "chord_spans": [
                {
                    "label": label,
                    "start_frame": start,
                    "end_frame": end,
                }
                for label, start, end in self.chord_spans
            ],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MeasureProjection:
        frame_range = value.get("range")
        spans = value.get("chord_spans")
        if (
            set(value) != {
                "measure_number",
                "range",
                "section_name",
                "section_ordinal",
                "chord_spans",
            }
            or not isinstance(frame_range, Mapping)
            or set(frame_range) != {"start_frame", "end_frame"}
            or not isinstance(spans, list)
        ):
            raise ValueError("measure projection record is invalid")
        parsed = []
        for item in spans:
            if not isinstance(item, Mapping) or set(item) != {
                "label",
                "start_frame",
                "end_frame",
            }:
                raise ValueError("measure chord span record is invalid")
            parsed.append(
                (
                    str(item["label"]),
                    _integer(item["start_frame"]),
                    _integer(item["end_frame"]),
                )
            )
        return cls(
            measure_number=_integer(value["measure_number"]),
            start_frame=_integer(frame_range["start_frame"]),
            end_frame=_integer(frame_range["end_frame"]),
            section_name=str(value["section_name"]),
            section_ordinal=_integer(value["section_ordinal"]),
            chord_spans=tuple(parsed),
        )


@dataclass(frozen=True)
class PromotionSpec:
    id: str
    session_id: str
    review_revision_id: str
    reviewed_timeline_id: str
    mapping_config_id: str

    def __post_init__(self) -> None:
        if (
            not self.id.startswith("sha256:")
            or _SESSION_RE.fullmatch(self.session_id) is None
            or _REVISION_RE.fullmatch(self.review_revision_id) is None
            or _TIMELINE_RE.fullmatch(self.reviewed_timeline_id) is None
            or not self.mapping_config_id.startswith("sha256:")
        ):
            raise ValueError("promotion spec identity is invalid")
        if self.id != f"sha256:{content_digest(self.identity_mapping())}":
            raise ValueError("promotion spec id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        review_revision_id: str,
        reviewed_timeline_id: str,
        mapping_config_id: str,
    ) -> PromotionSpec:
        identity = {
            "promotion_spec_schema_version": "1.0.0-draft",
            "session_id": session_id,
            "review_revision_id": review_revision_id,
            "reviewed_timeline_id": reviewed_timeline_id,
            "mapping_config_id": mapping_config_id,
        }
        return cls(
            id=f"sha256:{content_digest(identity)}",
            session_id=session_id,
            review_revision_id=review_revision_id,
            reviewed_timeline_id=reviewed_timeline_id,
            mapping_config_id=mapping_config_id,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "promotion_spec_schema_version": "1.0.0-draft",
            "session_id": self.session_id,
            "review_revision_id": self.review_revision_id,
            "reviewed_timeline_id": self.reviewed_timeline_id,
            "mapping_config_id": self.mapping_config_id,
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PromotionSpec:
        expected = {
            "id",
            "promotion_spec_schema_version",
            "session_id",
            "review_revision_id",
            "reviewed_timeline_id",
            "mapping_config_id",
        }
        if (
            set(value) != expected
            or value["promotion_spec_schema_version"] != "1.0.0-draft"
        ):
            raise ValueError("promotion spec record is invalid")
        return cls(
            id=str(value["id"]),
            session_id=str(value["session_id"]),
            review_revision_id=str(value["review_revision_id"]),
            reviewed_timeline_id=str(value["reviewed_timeline_id"]),
            mapping_config_id=str(value["mapping_config_id"]),
        )


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    session_id: str
    review_revision_id: str
    reviewed_timeline_id: str
    mapping_config_id: str
    spec_id: str
    result_id: str
    receipt_id: str
    issue_digest: str
    acknowledged_issue_ids: tuple[str, ...]
    approved_at: str
    actor_kind: str = "local_user"

    def __post_init__(self) -> None:
        if (
            _APPROVAL_RE.fullmatch(self.id) is None
            or _SESSION_RE.fullmatch(self.session_id) is None
            or _REVISION_RE.fullmatch(self.review_revision_id) is None
            or _TIMELINE_RE.fullmatch(self.reviewed_timeline_id) is None
            or not self.mapping_config_id.startswith("sha256:")
            or not self.spec_id.startswith("sha256:")
            or _RESULT_RE.fullmatch(self.result_id) is None
            or _RECEIPT_RE.fullmatch(self.receipt_id) is None
            or not self.issue_digest.startswith("sha256:")
        ):
            raise ValueError("approval identity is invalid")
        if (
            len(self.acknowledged_issue_ids) > MAX_ACKNOWLEDGEMENTS
            or self.acknowledged_issue_ids
            != tuple(sorted(set(self.acknowledged_issue_ids)))
        ):
            raise ValueError("approval acknowledgements are invalid")
        if self.actor_kind != "local_user":
            raise ValueError("approval actor or time is invalid")
        _timestamp(self.approved_at)
        if self.id != f"apr_{content_digest(self.identity_mapping())}":
            raise ValueError("approval id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        review_revision_id: str,
        reviewed_timeline_id: str,
        mapping_config_id: str,
        spec_id: str,
        result_id: str,
        receipt_id: str,
        issue_digest: str,
        acknowledged_issue_ids: tuple[str, ...],
        approved_at: str,
    ) -> ApprovalRecord:
        identity = {
            "approval_schema_version": "1.0.0-draft",
            "session_id": session_id,
            "review_revision_id": review_revision_id,
            "reviewed_timeline_id": reviewed_timeline_id,
            "mapping_config_id": mapping_config_id,
            "spec_id": spec_id,
            "result_id": result_id,
            "receipt_id": receipt_id,
            "issue_digest": issue_digest,
            "acknowledged_issue_ids": list(sorted(set(acknowledged_issue_ids))),
            "approved_at": approved_at,
            "actor_kind": "local_user",
        }
        return cls(
            id=f"apr_{content_digest(identity)}",
            session_id=session_id,
            review_revision_id=review_revision_id,
            reviewed_timeline_id=reviewed_timeline_id,
            mapping_config_id=mapping_config_id,
            spec_id=spec_id,
            result_id=result_id,
            receipt_id=receipt_id,
            issue_digest=issue_digest,
            acknowledged_issue_ids=tuple(identity["acknowledged_issue_ids"]),
            approved_at=approved_at,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "approval_schema_version": "1.0.0-draft",
            "session_id": self.session_id,
            "review_revision_id": self.review_revision_id,
            "reviewed_timeline_id": self.reviewed_timeline_id,
            "mapping_config_id": self.mapping_config_id,
            "spec_id": self.spec_id,
            "result_id": self.result_id,
            "receipt_id": self.receipt_id,
            "issue_digest": self.issue_digest,
            "acknowledged_issue_ids": list(self.acknowledged_issue_ids),
            "approved_at": self.approved_at,
            "actor_kind": self.actor_kind,
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ApprovalRecord:
        expected = {
            "id",
            "approval_schema_version",
            "session_id",
            "review_revision_id",
            "reviewed_timeline_id",
            "mapping_config_id",
            "spec_id",
            "result_id",
            "receipt_id",
            "issue_digest",
            "acknowledged_issue_ids",
            "approved_at",
            "actor_kind",
        }
        if (
            set(value) != expected
            or value["approval_schema_version"] != "1.0.0-draft"
            or not isinstance(value["acknowledged_issue_ids"], list)
        ):
            raise ValueError("approval record is invalid")
        return cls(
            id=str(value["id"]),
            session_id=str(value["session_id"]),
            review_revision_id=str(value["review_revision_id"]),
            reviewed_timeline_id=str(value["reviewed_timeline_id"]),
            mapping_config_id=str(value["mapping_config_id"]),
            spec_id=str(value["spec_id"]),
            result_id=str(value["result_id"]),
            receipt_id=str(value["receipt_id"]),
            issue_digest=str(value["issue_digest"]),
            acknowledged_issue_ids=tuple(
                str(item) for item in value["acknowledged_issue_ids"]
            ),
            approved_at=str(value["approved_at"]),
            actor_kind=str(value["actor_kind"]),
        )


@dataclass(frozen=True)
class PromotionResult:
    id: str
    spec_id: str
    chart: SongChart
    issues: tuple[PromotionIssue, ...]
    timing_map: tuple[MeasureProjection, ...]

    def __post_init__(self) -> None:
        if _RESULT_RE.fullmatch(self.id) is None or not self.spec_id.startswith("sha256:"):
            raise ValueError("promotion result identity is invalid")
        if self.id != f"pro_{content_digest(self.identity_mapping())}":
            raise ValueError("promotion result id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        spec_id: str,
        chart: SongChart,
        issues: tuple[PromotionIssue, ...],
        timing_map: tuple[MeasureProjection, ...],
    ) -> PromotionResult:
        identity = {
            "promotion_result_schema_version": "1.0.0-draft",
            "spec_id": spec_id,
            "chart": chart.to_mapping(),
            "issues": [item.to_mapping() for item in issues],
            "timing_map": [item.to_mapping() for item in timing_map],
        }
        return cls(
            id=f"pro_{content_digest(identity)}",
            spec_id=spec_id,
            chart=chart,
            issues=issues,
            timing_map=timing_map,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "promotion_result_schema_version": "1.0.0-draft",
            "spec_id": self.spec_id,
            "chart": self.chart.to_mapping(),
            "issues": [item.to_mapping() for item in self.issues],
            "timing_map": [item.to_mapping() for item in self.timing_map],
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "promotion_result_schema_version": "1.0.0-draft",
            "chart": self.chart.to_mapping(),
            "issues": [item.to_mapping() for item in self.issues],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PromotionResult:
        expected = {
            "id",
            "promotion_result_schema_version",
            "spec_id",
            "chart",
            "issues",
            "timing_map",
        }
        if (
            set(value) != expected
            or value["promotion_result_schema_version"] != "1.0.0-draft"
            or not isinstance(value["chart"], dict)
            or not isinstance(value["issues"], list)
            or not isinstance(value["timing_map"], list)
        ):
            raise ValueError("promotion result record is invalid")
        return cls(
            id=str(value["id"]),
            spec_id=str(value["spec_id"]),
            chart=SongChart.from_mapping(value["chart"]),
            issues=tuple(PromotionIssue.from_mapping(item) for item in value["issues"]),
            timing_map=tuple(
                MeasureProjection.from_mapping(item) for item in value["timing_map"]
            ),
        )


@dataclass(frozen=True)
class RevocationRecord:
    id: str
    approval_id: str
    revoked_at: str
    reason: str

    def __post_init__(self) -> None:
        if (
            _REVOCATION_RE.fullmatch(self.id) is None
            or _APPROVAL_RE.fullmatch(self.approval_id) is None
        ):
            raise ValueError("revocation identity is invalid")
        _text(self.reason, "revocation reason")
        _timestamp(self.revoked_at)
        if self.id != f"rev_{content_digest(self.identity_mapping())}":
            raise ValueError("revocation content is invalid")

    @classmethod
    def create(
        cls, *, approval_id: str, revoked_at: str, reason: str
    ) -> RevocationRecord:
        identity = {
            "revocation_schema_version": "1.0.0-draft",
            "approval_id": approval_id,
            "revoked_at": revoked_at,
            "reason": _text(reason, "revocation reason"),
            "actor_kind": "local_user",
        }
        return cls(
            id=f"rev_{content_digest(identity)}",
            approval_id=approval_id,
            revoked_at=revoked_at,
            reason=identity["reason"],
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "revocation_schema_version": "1.0.0-draft",
            "approval_id": self.approval_id,
            "revoked_at": self.revoked_at,
            "reason": self.reason,
            "actor_kind": "local_user",
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RevocationRecord:
        expected = {
            "id",
            "revocation_schema_version",
            "approval_id",
            "revoked_at",
            "reason",
            "actor_kind",
        }
        if (
            set(value) != expected
            or value["revocation_schema_version"] != "1.0.0-draft"
            or value["actor_kind"] != "local_user"
        ):
            raise ValueError("revocation record is invalid")
        return cls(
            id=str(value["id"]),
            approval_id=str(value["approval_id"]),
            revoked_at=str(value["revoked_at"]),
            reason=str(value["reason"]),
        )


def issues_digest(issues: tuple[PromotionIssue, ...]) -> str:
    return f"sha256:{content_digest([item.to_mapping() for item in issues])}"


def canonical_chart_bytes(chart: SongChart) -> bytes:
    return canonical_json(chart.to_mapping())


def _text(value: Any, name: str, *, limit: int = MAX_TEXT) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
        or any(ord(character) < 32 for character in value)
    ):
        raise PromotionError("invalid_mapping", f"{name.title()} is invalid.")
    return value


def _integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PromotionError("invalid_mapping", "Mapping integer field is invalid.")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("promotion timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("promotion timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("promotion timestamp must include an offset")
    return value
