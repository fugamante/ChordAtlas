from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping

from chordatlas.analysis.models import ChordCandidateTimeline
from chordatlas.media.models import FrameRange, Timebase

_SESSION_RE = re.compile(r"^review_[0-9a-f]{32}$")
_REVISION_RE = re.compile(r"^rrv_[0-9a-f]{64}$")
_EDIT_RE = re.compile(r"^red_[0-9a-f]{64}$")
_TIMELINE_RE = re.compile(r"^rtl_[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^rseg_[0-9a-f]{24}$")
_MARKER_RE = re.compile(r"^mark_[0-9a-f]{24}$")
_RUN_RE = re.compile(r"^run_[0-9a-f]{32}$")
_SOURCE_RE = re.compile(r"^src_[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANONICAL_CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:(?::(?:maj|min))|(?:maj|min|m|dim|aug|sus|add)?[0-9]*)?"
    r"(?:/[A-G](?:#|b)?)?$"
)

MAX_REVISIONS = 512
MAX_SEGMENTS = 2_000
MAX_MARKERS = 1_000
MAX_LABEL_BYTES = 96


class ReviewError(ValueError):
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
    _validate_json(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class ReviewSegment:
    id: str
    frame_range: FrameRange
    state: str
    label: str | None
    label_status: str
    start_boundary_status: str
    decision_origin: str
    origin_ordinals: tuple[int, ...]
    selected_candidate_rank: int | None = None

    def __post_init__(self) -> None:
        if _SEGMENT_RE.fullmatch(self.id) is None:
            raise ValueError("review segment id is invalid")
        if self.state not in {"chord", "no_chord", "unknown"}:
            raise ValueError("review segment state is invalid")
        if self.state == "chord" and self.label is None:
            raise ValueError("chord review segment requires a label")
        if self.state != "chord" and self.label is not None:
            raise ValueError("non-chord review segment cannot carry a label")
        if self.label is not None:
            _normalize_label(self.label)
        if self.label_status not in {"unreviewed", "reviewed", "unresolved"}:
            raise ValueError("review segment label status is invalid")
        if self.start_boundary_status not in {"machine", "reviewed"}:
            raise ValueError("review segment boundary status is invalid")
        if self.decision_origin not in {
            "machine_primary",
            "machine_no_chord",
            "machine_unknown",
            "primary_confirmed",
            "candidate_selected",
            "manual_label",
            "human_no_chord",
            "human_unknown",
            "human_merge",
        }:
            raise ValueError("review segment decision origin is invalid")
        if (
            not self.origin_ordinals
            or tuple(sorted(set(self.origin_ordinals))) != self.origin_ordinals
            or any(item < 0 for item in self.origin_ordinals)
        ):
            raise ValueError("review segment raw lineage is invalid")
        if self.selected_candidate_rank is not None and self.selected_candidate_rank <= 0:
            raise ValueError("selected candidate rank is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "range": self.frame_range.to_mapping(),
            "state": self.state,
            "label": self.label,
            "label_status": self.label_status,
            "start_boundary_status": self.start_boundary_status,
            "decision_origin": self.decision_origin,
            "origin_ordinals": list(self.origin_ordinals),
            "selected_candidate_rank": self.selected_candidate_rank,
        }


@dataclass(frozen=True)
class SectionMarker:
    id: str
    frame: int
    label: str

    def __post_init__(self) -> None:
        if _MARKER_RE.fullmatch(self.id) is None:
            raise ValueError("section marker id is invalid")
        if not isinstance(self.frame, int) or isinstance(self.frame, bool) or self.frame < 0:
            raise ValueError("section marker frame is invalid")
        _normalize_section_label(self.label)

    def to_mapping(self) -> dict[str, Any]:
        return {"id": self.id, "frame": self.frame, "label": self.label}


@dataclass(frozen=True)
class ReviewedTimeline:
    id: str
    base_timeline_id: str
    timebase: Timebase
    analyzed_range: FrameRange
    phase: str
    segments: tuple[ReviewSegment, ...]
    section_markers: tuple[SectionMarker, ...]

    def __post_init__(self) -> None:
        if _TIMELINE_RE.fullmatch(self.id) is None or _DIGEST_RE.fullmatch(
            self.base_timeline_id
        ) is None:
            raise ValueError("reviewed timeline identity is invalid")
        self.timebase.validate_range(self.analyzed_range)
        if self.phase not in {"unreviewed", "in_review", "ready_for_approval"}:
            raise ValueError("review phase is invalid")
        if not self.segments or len(self.segments) > MAX_SEGMENTS:
            raise ValueError("review segment count is invalid")
        if len(self.section_markers) > MAX_MARKERS:
            raise ValueError("section marker count is invalid")
        expected = self.analyzed_range.start_frame
        segment_ids: set[str] = set()
        for segment in self.segments:
            if segment.id in segment_ids or segment.frame_range.start_frame != expected:
                raise ValueError("review segments must be unique, ordered, and contiguous")
            segment_ids.add(segment.id)
            expected = segment.frame_range.end_frame
        if expected != self.analyzed_range.end_frame:
            raise ValueError("review segments must cover the analyzed range")
        marker_ids: set[str] = set()
        marker_frames: set[int] = set()
        for marker in self.section_markers:
            if (
                marker.id in marker_ids
                or marker.frame in marker_frames
                or not self.analyzed_range.start_frame
                <= marker.frame
                < self.analyzed_range.end_frame
            ):
                raise ValueError("section markers must be unique and in range")
            marker_ids.add(marker.id)
            marker_frames.add(marker.frame)
        if self.section_markers != tuple(
            sorted(self.section_markers, key=lambda item: (item.frame, item.id))
        ):
            raise ValueError("section markers must be ordered")
        if self.id != f"rtl_{content_digest(self.identity_mapping())}":
            raise ValueError("reviewed timeline id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        base_timeline_id: str,
        timebase: Timebase,
        analyzed_range: FrameRange,
        phase: str,
        segments: tuple[ReviewSegment, ...],
        section_markers: tuple[SectionMarker, ...] = (),
    ) -> ReviewedTimeline:
        identity = _timeline_identity(
            base_timeline_id,
            timebase,
            analyzed_range,
            phase,
            segments,
            section_markers,
        )
        return cls(
            id=f"rtl_{content_digest(identity)}",
            base_timeline_id=base_timeline_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            phase=phase,
            segments=segments,
            section_markers=section_markers,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return _timeline_identity(
            self.base_timeline_id,
            self.timebase,
            self.analyzed_range,
            self.phase,
            self.segments,
            self.section_markers,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "reviewed_timeline_schema_version": "1.0.0-draft",
            "timebase": self.timebase.to_mapping(),
            "analyzed_range": self.analyzed_range.to_mapping(),
            "phase": self.phase,
            "segments": [item.to_mapping() for item in self.segments],
            "section_markers": [item.to_mapping() for item in self.section_markers],
            "summary": self.summary_mapping(),
        }

    def summary_mapping(self) -> dict[str, int]:
        return {
            "unreviewed_segments": sum(
                item.label_status == "unreviewed" for item in self.segments
            ),
            "reviewed_segments": sum(item.label_status == "reviewed" for item in self.segments),
            "unresolved_segments": sum(
                item.label_status == "unresolved" for item in self.segments
            ),
            "machine_boundaries": sum(
                item.start_boundary_status == "machine" for item in self.segments[1:]
            ),
            "reviewed_boundaries": sum(
                item.start_boundary_status == "reviewed" for item in self.segments[1:]
            ),
            "section_markers": len(self.section_markers),
        }


@dataclass(frozen=True)
class ReviewEdit:
    id: str
    kind: str
    parameters: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        normalized = _normalize_parameters(self.kind, dict(self.parameters))
        if tuple(normalized.items()) != self.parameters:
            raise ValueError("review edit parameters are not canonical")
        if _EDIT_RE.fullmatch(self.id) is None:
            raise ValueError("review edit id is invalid")
        if self.id != f"red_{content_digest(self.identity_mapping())}":
            raise ValueError("review edit id does not match its content")

    @classmethod
    def create(cls, kind: str, parameters: Mapping[str, Any]) -> ReviewEdit:
        normalized = _normalize_parameters(kind, parameters)
        identity = {
            "review_edit_schema_version": "1.0.0-draft",
            "actor_kind": "local_user",
            "kind": kind,
            "parameters": normalized,
        }
        return cls(
            id=f"red_{content_digest(identity)}",
            kind=kind,
            parameters=tuple(normalized.items()),
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "review_edit_schema_version": "1.0.0-draft",
            "actor_kind": "local_user",
            "kind": self.kind,
            "parameters": dict(self.parameters),
        }

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}


@dataclass(frozen=True)
class ReviewRevision:
    id: str
    session_id: str
    parent_revision_id: str | None
    edit_id: str | None
    timeline_id: str

    def __post_init__(self) -> None:
        if _REVISION_RE.fullmatch(self.id) is None or _SESSION_RE.fullmatch(
            self.session_id
        ) is None:
            raise ValueError("review revision identity is invalid")
        if self.parent_revision_id is not None and _REVISION_RE.fullmatch(
            self.parent_revision_id
        ) is None:
            raise ValueError("review parent revision id is invalid")
        if self.edit_id is not None and _EDIT_RE.fullmatch(self.edit_id) is None:
            raise ValueError("review revision edit id is invalid")
        if (self.parent_revision_id is None) != (self.edit_id is None):
            raise ValueError("only the root revision may omit parent and edit")
        if _TIMELINE_RE.fullmatch(self.timeline_id) is None:
            raise ValueError("review revision timeline id is invalid")
        if self.id != f"rrv_{content_digest(self.identity_mapping())}":
            raise ValueError("review revision id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        parent_revision_id: str | None,
        edit_id: str | None,
        timeline_id: str,
    ) -> ReviewRevision:
        identity = _revision_identity(
            session_id,
            parent_revision_id,
            edit_id,
            timeline_id,
        )
        return cls(
            id=f"rrv_{content_digest(identity)}",
            session_id=session_id,
            parent_revision_id=parent_revision_id,
            edit_id=edit_id,
            timeline_id=timeline_id,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return _revision_identity(
            self.session_id,
            self.parent_revision_id,
            self.edit_id,
            self.timeline_id,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.identity_mapping()}


@dataclass(frozen=True)
class ReviewSession:
    id: str
    source_id: str
    analysis_run_id: str
    base_timeline_id: str
    root_revision_id: str
    created_at: str

    def __post_init__(self) -> None:
        if (
            _SESSION_RE.fullmatch(self.id) is None
            or _SOURCE_RE.fullmatch(self.source_id) is None
            or _RUN_RE.fullmatch(self.analysis_run_id) is None
            or _DIGEST_RE.fullmatch(self.base_timeline_id) is None
            or _REVISION_RE.fullmatch(self.root_revision_id) is None
        ):
            raise ValueError("review session identity is invalid")
        _validate_timestamp(self.created_at)

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        analysis_run_id: str,
        base_timeline_id: str,
        root_revision_id: str,
        created_at: str,
    ) -> ReviewSession:
        return cls(
            id=f"review_{secrets.token_hex(16)}",
            source_id=source_id,
            analysis_run_id=analysis_run_id,
            base_timeline_id=base_timeline_id,
            root_revision_id=root_revision_id,
            created_at=created_at,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "review_session_schema_version": "1.0.0-draft",
            "id": self.id,
            "source_id": self.source_id,
            "analysis_run_id": self.analysis_run_id,
            "base_timeline_id": self.base_timeline_id,
            "root_revision_id": self.root_revision_id,
            "created_at": self.created_at,
        }

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "source_id": self.source_id,
            "analysis_run_id": self.analysis_run_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ReviewHead:
    session_id: str
    generation: int
    revision_id: str
    redo_revision_ids: tuple[str, ...]
    accepted_edit_events: int
    undo_events: int
    redo_events: int
    reset_events: int
    updated_at: str
    ready_at: str | None = None

    def __post_init__(self) -> None:
        if _SESSION_RE.fullmatch(self.session_id) is None or self.generation < 0:
            raise ValueError("review head identity is invalid")
        if _REVISION_RE.fullmatch(self.revision_id) is None:
            raise ValueError("review head revision is invalid")
        if len(self.redo_revision_ids) > MAX_REVISIONS or any(
            _REVISION_RE.fullmatch(item) is None for item in self.redo_revision_ids
        ):
            raise ValueError("review redo path is invalid")
        counts = (
            self.accepted_edit_events,
            self.undo_events,
            self.redo_events,
            self.reset_events,
        )
        if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in counts):
            raise ValueError("review event counts are invalid")
        _validate_timestamp(self.updated_at)
        if self.ready_at is not None:
            _validate_timestamp(self.ready_at)

    @property
    def token(self) -> str:
        return f"head_{self.generation}_{self.revision_id[-16:]}"

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "review_head_schema_version": "1.0.0-draft",
            "session_id": self.session_id,
            "generation": self.generation,
            "revision_id": self.revision_id,
            "redo_revision_ids": list(self.redo_revision_ids),
            "accepted_edit_events": self.accepted_edit_events,
            "undo_events": self.undo_events,
            "redo_events": self.redo_events,
            "reset_events": self.reset_events,
            "updated_at": self.updated_at,
            "ready_at": self.ready_at,
        }


def root_timeline(base: ChordCandidateTimeline) -> ReviewedTimeline:
    if not base.segments:
        raise ReviewError(
            "review_unavailable",
            "A review requires at least one candidate segment.",
        )
    if len(base.segments) > MAX_SEGMENTS:
        raise ReviewError(
            "review_limit",
            "The candidate timeline has too many segments for local review.",
        )
    segments = []
    for item in base.segments:
        primary = item.candidates[0] if item.candidates else None
        if item.state == "chord" and primary is not None:
            label = primary.canonical_symbol or primary.raw_label
            status = "unreviewed" if primary.canonical_symbol is not None else "unresolved"
            state = "chord"
            origin = "machine_primary"
        elif item.state == "no_chord":
            label = None
            status = "unreviewed"
            state = "no_chord"
            origin = "machine_no_chord"
        else:
            label = None
            status = "unresolved"
            state = "unknown"
            origin = "machine_unknown"
        segments.append(
            ReviewSegment(
                id=_derived_segment_id(base.id, str(item.ordinal)),
                frame_range=item.frame_range,
                state=state,
                label=label,
                label_status=status,
                start_boundary_status="machine",
                decision_origin=origin,
                origin_ordinals=(item.ordinal,),
                selected_candidate_rank=1 if primary is not None else None,
            )
        )
    return ReviewedTimeline.create(
        base_timeline_id=base.id,
        timebase=base.timebase,
        analyzed_range=base.analyzed_range,
        phase="unreviewed",
        segments=tuple(segments),
    )


def apply_edit(
    current: ReviewedTimeline,
    edit: ReviewEdit,
    *,
    base: ChordCandidateTimeline,
    raw_root: ReviewedTimeline,
) -> ReviewedTimeline:
    if current.base_timeline_id != base.id or raw_root.base_timeline_id != base.id:
        raise ReviewError("review_integrity", "Review base timeline does not match.")
    parameters = dict(edit.parameters)
    segments = list(current.segments)
    markers = list(current.section_markers)
    phase = "in_review"

    if edit.kind == "accept_current":
        index = _segment_index(segments, parameters["segment_id"])
        segment = segments[index]
        if segment.state == "unknown":
            raise ReviewError("review_unresolved", "Resolve Unknown before accepting it.")
        segments[index] = replace(
            segment,
            label_status="reviewed",
            decision_origin=(
                "primary_confirmed"
                if segment.decision_origin == "machine_primary"
                else segment.decision_origin
            ),
        )
    elif edit.kind == "select_candidate":
        index = _segment_index(segments, parameters["segment_id"])
        segment = segments[index]
        if len(segment.origin_ordinals) != 1:
            raise ReviewError(
                "candidate_ambiguous",
                "Choose a manual label after merging multiple machine segments.",
            )
        raw = base.segments[segment.origin_ordinals[0]]
        rank = parameters["candidate_rank"]
        candidate = next((item for item in raw.candidates if item.rank == rank), None)
        if candidate is None:
            raise ReviewError("candidate_unavailable", "That machine candidate is unavailable.")
        label = candidate.canonical_symbol or candidate.raw_label
        segments[index] = replace(
            segment,
            state="chord",
            label=label,
            label_status=(
                "reviewed" if candidate.canonical_symbol is not None else "unresolved"
            ),
            decision_origin=(
                "primary_confirmed" if rank == 1 else "candidate_selected"
            ),
            selected_candidate_rank=rank,
        )
    elif edit.kind == "set_label":
        index = _segment_index(segments, parameters["segment_id"])
        label = parameters["label"]
        if label.casefold() in {"n.c.", "unknown"}:
            raise ReviewError(
                "reserved_chord_label",
                "Use Mark N.C. or Mark Unknown for that review state.",
            )
        segments[index] = replace(
            segments[index],
            state="chord",
            label=label,
            label_status="reviewed" if _is_canonical_chord(label) else "unresolved",
            decision_origin="manual_label",
            selected_candidate_rank=None,
        )
    elif edit.kind == "set_no_chord":
        index = _segment_index(segments, parameters["segment_id"])
        segments[index] = replace(
            segments[index],
            state="no_chord",
            label=None,
            label_status="reviewed",
            decision_origin="human_no_chord",
            selected_candidate_rank=None,
        )
    elif edit.kind == "set_unknown":
        index = _segment_index(segments, parameters["segment_id"])
        segments[index] = replace(
            segments[index],
            state="unknown",
            label=None,
            label_status="unresolved",
            decision_origin="human_unknown",
            selected_candidate_rank=None,
        )
    elif edit.kind == "split":
        if len(segments) >= MAX_SEGMENTS:
            raise ReviewError("review_limit", "The review reached its segment limit.")
        index = _segment_index(segments, parameters["segment_id"])
        segment = segments[index]
        frame = parameters["frame"]
        if not segment.frame_range.start_frame < frame < segment.frame_range.end_frame:
            raise ReviewError(
                "invalid_split",
                "Split frame must be strictly inside the selected segment.",
            )
        left = replace(
            segment,
            id=_derived_segment_id(edit.id, "left"),
            frame_range=FrameRange(segment.frame_range.start_frame, frame),
        )
        right = replace(
            segment,
            id=_derived_segment_id(edit.id, "right"),
            frame_range=FrameRange(frame, segment.frame_range.end_frame),
            start_boundary_status="reviewed",
        )
        segments[index : index + 1] = [left, right]
    elif edit.kind == "merge":
        left_index = _adjacent_indexes(
            segments,
            parameters["left_segment_id"],
            parameters["right_segment_id"],
        )
        left = segments[left_index]
        right = segments[left_index + 1]
        if left.state != right.state or left.label != right.label:
            raise ReviewError(
                "merge_incompatible",
                "Only adjacent segments with the same effective chord can be merged.",
            )
        merged = ReviewSegment(
            id=_derived_segment_id(edit.id, "merged"),
            frame_range=FrameRange(
                left.frame_range.start_frame,
                right.frame_range.end_frame,
            ),
            state=left.state,
            label=left.label,
            label_status=(
                "unresolved"
                if "unresolved" in {left.label_status, right.label_status}
                else "reviewed"
            ),
            start_boundary_status=left.start_boundary_status,
            decision_origin="human_merge",
            origin_ordinals=tuple(
                sorted(set(left.origin_ordinals + right.origin_ordinals))
            ),
            selected_candidate_rank=(
                left.selected_candidate_rank
                if left.selected_candidate_rank == right.selected_candidate_rank
                else None
            ),
        )
        segments[left_index : left_index + 2] = [merged]
    elif edit.kind == "move_boundary":
        left_index = _adjacent_indexes(
            segments,
            parameters["left_segment_id"],
            parameters["right_segment_id"],
        )
        left = segments[left_index]
        right = segments[left_index + 1]
        frame = parameters["frame"]
        if not left.frame_range.start_frame < frame < right.frame_range.end_frame:
            raise ReviewError(
                "invalid_boundary",
                "Boundary must leave both adjacent segments with positive duration.",
            )
        segments[left_index] = replace(
            left,
            frame_range=FrameRange(left.frame_range.start_frame, frame),
        )
        segments[left_index + 1] = replace(
            right,
            frame_range=FrameRange(frame, right.frame_range.end_frame),
            start_boundary_status="reviewed",
        )
    elif edit.kind == "move_segment":
        index = _segment_index(segments, parameters["segment_id"])
        if index == 0 or index == len(segments) - 1:
            raise ReviewError(
                "move_edge_segment",
                "Only an interior segment can move with adjacent adjustment.",
            )
        if parameters["resolution"] != "adjust_adjacent":
            raise ReviewError(
                "resolution_required",
                "Confirm Adjust adjacent segments before moving this segment.",
            )
        left = segments[index - 1]
        segment = segments[index]
        right = segments[index + 1]
        start = parameters["start_frame"]
        end = start + segment.frame_range.length_frames
        if not left.frame_range.start_frame < start < end < right.frame_range.end_frame:
            raise ReviewError(
                "move_collision",
                "The move would consume a neighbor or leave the analyzed range.",
            )
        segments[index - 1] = replace(
            left,
            frame_range=FrameRange(left.frame_range.start_frame, start),
        )
        segments[index] = replace(
            segment,
            frame_range=FrameRange(start, end),
            start_boundary_status="reviewed",
        )
        segments[index + 1] = replace(
            right,
            frame_range=FrameRange(end, right.frame_range.end_frame),
            start_boundary_status="reviewed",
        )
    elif edit.kind == "insert_no_chord":
        start = parameters["start_frame"]
        end = parameters["end_frame"]
        if start < 0 or end <= start:
            raise ReviewError(
                "invalid_no_chord_range",
                "N.C. range must have positive in-range duration.",
            )
        segments = _replace_range_with_no_chord(
            segments,
            FrameRange(start, end),
            edit.id,
            current.analyzed_range,
        )
        if len(segments) > MAX_SEGMENTS:
            raise ReviewError("review_limit", "The review reached its segment limit.")
    elif edit.kind == "add_section":
        if len(markers) >= MAX_MARKERS:
            raise ReviewError("review_limit", "The review reached its section limit.")
        _validate_marker_frame(current, markers, parameters["frame"])
        marker = SectionMarker(
            _derived_marker_id(edit.id, "section"),
            parameters["frame"],
            parameters["label"],
        )
        markers.append(marker)
    elif edit.kind == "rename_section":
        index = _marker_index(markers, parameters["marker_id"])
        markers[index] = replace(markers[index], label=parameters["label"])
    elif edit.kind == "move_section":
        index = _marker_index(markers, parameters["marker_id"])
        frame = parameters["frame"]
        _validate_marker_frame(current, markers, frame, ignore_id=markers[index].id)
        markers[index] = replace(markers[index], frame=frame)
    elif edit.kind == "remove_section":
        index = _marker_index(markers, parameters["marker_id"])
        markers.pop(index)
    elif edit.kind == "reset_to_raw":
        segments = list(raw_root.segments)
        markers = []
    elif edit.kind == "accept_boundaries":
        segments = [
            replace(item, start_boundary_status="reviewed")
            if index > 0
            else item
            for index, item in enumerate(segments)
        ]
    elif edit.kind == "finish_review":
        if any(item.label_status == "unresolved" for item in segments):
            raise ReviewError(
                "review_unresolved",
                "Resolve every Unknown or unsupported label before finishing review.",
            )
        if any(item.label_status == "unreviewed" for item in segments):
            raise ReviewError(
                "review_unreviewed",
                "Accept or correct every machine proposal before finishing review.",
            )
        if any(item.start_boundary_status == "machine" for item in segments[1:]):
            raise ReviewError(
                "review_boundaries_unreviewed",
                "Accept or adjust every machine boundary before finishing review.",
            )
        phase = "ready_for_approval"
    else:
        raise ReviewError("unknown_review_edit", "Review operation is not supported.")

    markers = sorted(markers, key=lambda item: (item.frame, item.id))
    return ReviewedTimeline.create(
        base_timeline_id=current.base_timeline_id,
        timebase=current.timebase,
        analyzed_range=current.analyzed_range,
        phase=phase,
        segments=tuple(segments),
        section_markers=tuple(markers),
    )


def _replace_range_with_no_chord(
    segments: list[ReviewSegment],
    selected: FrameRange,
    edit_id: str,
    analyzed_range: FrameRange,
) -> list[ReviewSegment]:
    if (
        selected.start_frame < analyzed_range.start_frame
        or selected.end_frame > analyzed_range.end_frame
    ):
        raise ReviewError("invalid_no_chord_range", "N.C. range is outside the analysis.")
    result: list[ReviewSegment] = []
    origins: set[int] = set()
    inserted = False
    for index, segment in enumerate(segments):
        if segment.frame_range.end_frame <= selected.start_frame:
            result.append(segment)
            continue
        if segment.frame_range.start_frame >= selected.end_frame:
            if not inserted:
                result.append(_no_chord_segment(edit_id, selected, tuple(sorted(origins))))
                inserted = True
            result.append(segment)
            continue
        origins.update(segment.origin_ordinals)
        if segment.frame_range.start_frame < selected.start_frame:
            result.append(
                replace(
                    segment,
                    id=_derived_segment_id(edit_id, f"before-{index}"),
                    frame_range=FrameRange(
                        segment.frame_range.start_frame,
                        selected.start_frame,
                    ),
                )
            )
        if segment.frame_range.end_frame > selected.end_frame:
            if not inserted:
                result.append(_no_chord_segment(edit_id, selected, tuple(sorted(origins))))
                inserted = True
            result.append(
                replace(
                    segment,
                    id=_derived_segment_id(edit_id, f"after-{index}"),
                    frame_range=FrameRange(
                        selected.end_frame,
                        segment.frame_range.end_frame,
                    ),
                    start_boundary_status="reviewed",
                )
            )
    if not inserted:
        result.append(_no_chord_segment(edit_id, selected, tuple(sorted(origins))))
    return sorted(result, key=lambda item: item.frame_range.start_frame)


def _no_chord_segment(
    edit_id: str,
    frame_range: FrameRange,
    origins: tuple[int, ...],
) -> ReviewSegment:
    return ReviewSegment(
        id=_derived_segment_id(edit_id, "no-chord"),
        frame_range=frame_range,
        state="no_chord",
        label=None,
        label_status="reviewed",
        start_boundary_status="reviewed",
        decision_origin="human_no_chord",
        origin_ordinals=origins,
    )


def _normalize_parameters(kind: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        raise ReviewError("invalid_review_edit", "Review parameters must be an object.")
    schemas: dict[str, tuple[str, ...]] = {
        "accept_current": ("segment_id",),
        "select_candidate": ("candidate_rank", "segment_id"),
        "set_label": ("label", "segment_id"),
        "set_no_chord": ("segment_id",),
        "set_unknown": ("segment_id",),
        "split": ("frame", "segment_id"),
        "merge": ("left_segment_id", "right_segment_id"),
        "move_boundary": ("frame", "left_segment_id", "right_segment_id"),
        "move_segment": ("resolution", "segment_id", "start_frame"),
        "insert_no_chord": ("end_frame", "start_frame"),
        "add_section": ("frame", "label"),
        "rename_section": ("label", "marker_id"),
        "move_section": ("frame", "marker_id"),
        "remove_section": ("marker_id",),
        "reset_to_raw": (),
        "accept_boundaries": (),
        "finish_review": (),
    }
    expected = schemas.get(kind)
    if expected is None or set(parameters) != set(expected):
        raise ReviewError("invalid_review_edit", "Review operation fields are invalid.")
    normalized: dict[str, Any] = {}
    for name in expected:
        value = parameters[name]
        if name in {"frame", "start_frame", "end_frame", "candidate_rank"}:
            normalized[name] = _strict_int(value)
        elif name == "label":
            normalized[name] = (
                _normalize_section_label(value)
                if "section" in kind
                else _normalize_label(value)
            )
        elif name in {"segment_id", "left_segment_id", "right_segment_id"}:
            if not isinstance(value, str) or _SEGMENT_RE.fullmatch(value) is None:
                raise ReviewError("invalid_review_edit", "Review segment identifier is invalid.")
            normalized[name] = value
        elif name == "marker_id":
            if not isinstance(value, str) or _MARKER_RE.fullmatch(value) is None:
                raise ReviewError("invalid_review_edit", "Section marker identifier is invalid.")
            normalized[name] = value
        elif name == "resolution":
            if value != "adjust_adjacent":
                raise ReviewError("invalid_review_edit", "Move resolution is invalid.")
            normalized[name] = value
    return dict(sorted(normalized.items()))


def _normalize_label(value: Any) -> str:
    if not isinstance(value, str):
        raise ReviewError("invalid_chord_label", "Chord label must be text.")
    normalized = unicodedata.normalize("NFC", value.strip())
    if (
        not normalized
        or len(normalized.encode("utf-8")) > MAX_LABEL_BYTES
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise ReviewError(
            "invalid_chord_label",
            "Chord label must be short, non-empty text without control characters.",
        )
    return normalized


def _normalize_section_label(value: Any) -> str:
    normalized = _normalize_label(value)
    if len(normalized.encode("utf-8")) > 64:
        raise ReviewError("invalid_section_label", "Section label is too long.")
    return normalized


def _is_canonical_chord(value: str) -> bool:
    return value == "N.C." or _CANONICAL_CHORD_RE.fullmatch(value) is not None


def _strict_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReviewError("invalid_review_frame", "Review frames must be integers.")
    return value


def _segment_index(segments: list[ReviewSegment], segment_id: str) -> int:
    for index, segment in enumerate(segments):
        if segment.id == segment_id:
            return index
    raise ReviewError("review_segment_missing", "The selected review segment no longer exists.")


def _adjacent_indexes(
    segments: list[ReviewSegment],
    left_id: str,
    right_id: str,
) -> int:
    left_index = _segment_index(segments, left_id)
    if left_index + 1 >= len(segments) or segments[left_index + 1].id != right_id:
        raise ReviewError("review_not_adjacent", "Selected review segments are not adjacent.")
    return left_index


def _marker_index(markers: list[SectionMarker], marker_id: str) -> int:
    for index, marker in enumerate(markers):
        if marker.id == marker_id:
            return index
    raise ReviewError("section_missing", "The selected section marker no longer exists.")


def _validate_marker_frame(
    timeline: ReviewedTimeline,
    markers: list[SectionMarker],
    frame: int,
    *,
    ignore_id: str | None = None,
) -> None:
    if not timeline.analyzed_range.start_frame <= frame < timeline.analyzed_range.end_frame:
        raise ReviewError("invalid_section_frame", "Section marker is outside the analysis.")
    if any(item.frame == frame and item.id != ignore_id for item in markers):
        raise ReviewError(
            "duplicate_section_frame",
            "Only one section marker may occupy a frame in this release.",
        )


def _derived_segment_id(namespace: str, value: str) -> str:
    return f"rseg_{hashlib.sha256(f'{namespace}:{value}'.encode()).hexdigest()[:24]}"


def _derived_marker_id(namespace: str, value: str) -> str:
    return f"mark_{hashlib.sha256(f'{namespace}:{value}'.encode()).hexdigest()[:24]}"


def _timeline_identity(
    base_timeline_id: str,
    timebase: Timebase,
    analyzed_range: FrameRange,
    phase: str,
    segments: tuple[ReviewSegment, ...],
    section_markers: tuple[SectionMarker, ...],
) -> dict[str, Any]:
    return {
        "reviewed_timeline_schema_version": "1.0.0-draft",
        "base_timeline_id": base_timeline_id,
        "timebase": timebase.to_mapping(),
        "analyzed_range": analyzed_range.to_mapping(),
        "phase": phase,
        "segments": [item.to_mapping() for item in segments],
        "section_markers": [item.to_mapping() for item in section_markers],
    }


def _revision_identity(
    session_id: str,
    parent_revision_id: str | None,
    edit_id: str | None,
    timeline_id: str,
) -> dict[str, Any]:
    return {
        "review_revision_schema_version": "1.0.0-draft",
        "session_id": session_id,
        "parent_revision_id": parent_revision_id,
        "edit_id": edit_id,
        "timeline_id": timeline_id,
    }


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise ValueError("review contracts do not accept floats")
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("review mapping keys must be strings")
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError("review JSON contains an unsupported value")


def edit_from_mapping(value: dict[str, Any]) -> ReviewEdit:
    _expect_keys(
        value,
        {"id", "review_edit_schema_version", "actor_kind", "kind", "parameters"},
    )
    if (
        value["review_edit_schema_version"] != "1.0.0-draft"
        or value["actor_kind"] != "local_user"
        or not isinstance(value["kind"], str)
        or not isinstance(value["parameters"], dict)
    ):
        raise ValueError("review edit record is invalid")
    edit = ReviewEdit.create(value["kind"], value["parameters"])
    if value["id"] != edit.id:
        raise ValueError("review edit record digest is invalid")
    return edit


def timeline_from_mapping(value: dict[str, Any]) -> ReviewedTimeline:
    _expect_keys(
        value,
        {
            "id",
            "reviewed_timeline_schema_version",
            "base_timeline_id",
            "timebase",
            "analyzed_range",
            "phase",
            "segments",
            "section_markers",
        },
    )
    if value["reviewed_timeline_schema_version"] != "1.0.0-draft":
        raise ValueError("reviewed timeline version is invalid")
    timebase_value = _mapping(value["timebase"])
    range_value = _mapping(value["analyzed_range"])
    _expect_keys(timebase_value, {"unit", "sample_rate", "duration_frames"})
    _expect_keys(range_value, {"start_frame", "end_frame"})
    if timebase_value["unit"] != "sample_frame":
        raise ValueError("reviewed timeline timebase is invalid")
    segments_value = value["segments"]
    markers_value = value["section_markers"]
    if not isinstance(segments_value, list) or not isinstance(markers_value, list):
        raise ValueError("reviewed timeline collections are invalid")
    segments = []
    for item_value in segments_value:
        item = _mapping(item_value)
        _expect_keys(
            item,
            {
                "id",
                "range",
                "state",
                "label",
                "label_status",
                "start_boundary_status",
                "decision_origin",
                "origin_ordinals",
                "selected_candidate_rank",
            },
        )
        item_range = _mapping(item["range"])
        _expect_keys(item_range, {"start_frame", "end_frame"})
        origins = item["origin_ordinals"]
        if not isinstance(origins, list):
            raise ValueError("review segment lineage is invalid")
        rank = item["selected_candidate_rank"]
        segments.append(
            ReviewSegment(
                id=_string(item["id"]),
                frame_range=FrameRange(
                    _strict_record_int(item_range["start_frame"]),
                    _strict_record_int(item_range["end_frame"]),
                ),
                state=_string(item["state"]),
                label=None if item["label"] is None else _string(item["label"]),
                label_status=_string(item["label_status"]),
                start_boundary_status=_string(item["start_boundary_status"]),
                decision_origin=_string(item["decision_origin"]),
                origin_ordinals=tuple(_strict_record_int(value) for value in origins),
                selected_candidate_rank=(
                    None if rank is None else _strict_record_int(rank)
                ),
            )
        )
    markers = []
    for marker_value in markers_value:
        marker = _mapping(marker_value)
        _expect_keys(marker, {"id", "frame", "label"})
        markers.append(
            SectionMarker(
                _string(marker["id"]),
                _strict_record_int(marker["frame"]),
                _string(marker["label"]),
            )
        )
    return ReviewedTimeline(
        id=_string(value["id"]),
        base_timeline_id=_string(value["base_timeline_id"]),
        timebase=Timebase(
            _strict_record_int(timebase_value["sample_rate"]),
            _strict_record_int(timebase_value["duration_frames"]),
        ),
        analyzed_range=FrameRange(
            _strict_record_int(range_value["start_frame"]),
            _strict_record_int(range_value["end_frame"]),
        ),
        phase=_string(value["phase"]),
        segments=tuple(segments),
        section_markers=tuple(markers),
    )


def revision_from_mapping(value: dict[str, Any]) -> ReviewRevision:
    _expect_keys(
        value,
        {
            "id",
            "review_revision_schema_version",
            "session_id",
            "parent_revision_id",
            "edit_id",
            "timeline_id",
        },
    )
    if value["review_revision_schema_version"] != "1.0.0-draft":
        raise ValueError("review revision version is invalid")
    return ReviewRevision(
        id=_string(value["id"]),
        session_id=_string(value["session_id"]),
        parent_revision_id=(
            None
            if value["parent_revision_id"] is None
            else _string(value["parent_revision_id"])
        ),
        edit_id=None if value["edit_id"] is None else _string(value["edit_id"]),
        timeline_id=_string(value["timeline_id"]),
    )


def session_from_mapping(value: dict[str, Any]) -> ReviewSession:
    _expect_keys(
        value,
        {
            "review_session_schema_version",
            "id",
            "source_id",
            "analysis_run_id",
            "base_timeline_id",
            "root_revision_id",
            "created_at",
        },
    )
    if value["review_session_schema_version"] != "1.0.0-draft":
        raise ValueError("review session version is invalid")
    return ReviewSession(
        id=_string(value["id"]),
        source_id=_string(value["source_id"]),
        analysis_run_id=_string(value["analysis_run_id"]),
        base_timeline_id=_string(value["base_timeline_id"]),
        root_revision_id=_string(value["root_revision_id"]),
        created_at=_string(value["created_at"]),
    )


def head_from_mapping(value: dict[str, Any]) -> ReviewHead:
    _expect_keys(
        value,
        {
            "review_head_schema_version",
            "session_id",
            "generation",
            "revision_id",
            "redo_revision_ids",
            "accepted_edit_events",
            "undo_events",
            "redo_events",
            "reset_events",
            "updated_at",
            "ready_at",
        },
    )
    if value["review_head_schema_version"] != "1.0.0-draft":
        raise ValueError("review head version is invalid")
    redo = value["redo_revision_ids"]
    if not isinstance(redo, list):
        raise ValueError("review redo path is invalid")
    return ReviewHead(
        session_id=_string(value["session_id"]),
        generation=_strict_record_int(value["generation"]),
        revision_id=_string(value["revision_id"]),
        redo_revision_ids=tuple(_string(item) for item in redo),
        accepted_edit_events=_strict_record_int(value["accepted_edit_events"]),
        undo_events=_strict_record_int(value["undo_events"]),
        redo_events=_strict_record_int(value["redo_events"]),
        reset_events=_strict_record_int(value["reset_events"]),
        updated_at=_string(value["updated_at"]),
        ready_at=None if value["ready_at"] is None else _string(value["ready_at"]),
    )


def _expect_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("review record fields are invalid")


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("review record object is invalid")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("review record text is invalid")
    return value


def _strict_record_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("review record integer is invalid")
    return value


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("review timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("review timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("review timestamp must include an offset")
