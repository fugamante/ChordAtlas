from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from chordatlas.media.models import FrameRange, Timebase

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_RE = re.compile(r"^run_[0-9a-f]{32}$")
_SOURCE_RE = re.compile(r"^src_[0-9a-f]{32}$")
_SYMBOL_RE = re.compile(r"^[A-G](?:#|b)?(?::(?:maj|min))?$|^N\.C\.$")


class AnalysisError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
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


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise ValueError("hashed analysis contracts do not accept floats")
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("analysis mapping keys must be strings")
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError(f"unsupported analysis JSON value: {type(value).__name__}")


@dataclass(frozen=True)
class EngineRef:
    engine_id: str = "chordatlas.baseline"
    engine_version: str = "1.0.0"
    model_name: str = "template-chroma"
    model_version: str = "1.0.0"
    vocabulary_id: str = "maj-min-nc"
    vocabulary_version: str = "1.0.0"

    def __post_init__(self) -> None:
        for value in (
            self.engine_id,
            self.engine_version,
            self.model_name,
            self.model_version,
            self.vocabulary_id,
            self.vocabulary_version,
        ):
            if type(value) is not str or not value or len(value) > 80:
                raise ValueError("engine metadata is invalid")
        if self.engine_id != "chordatlas.baseline":
            raise ValueError("engine is not allowlisted")

    def to_mapping(self) -> dict[str, str]:
        return {
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "vocabulary_id": self.vocabulary_id,
            "vocabulary_version": self.vocabulary_version,
        }


@dataclass(frozen=True)
class AnalysisConfig:
    engine: EngineRef
    parameters: tuple[tuple[str, int], ...]
    random_seed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.engine, EngineRef):
            raise ValueError("analysis engine reference is invalid")
        allowed = {"target_rate", "window_frames", "hop_frames", "min_bpm", "max_bpm"}
        names = [name for name, _value in self.parameters]
        if names != sorted(names) or len(names) != len(set(names)) or set(names) != allowed:
            raise ValueError("analysis parameters must be the complete allowlisted set")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for _name, value in self.parameters
        ):
            raise ValueError("analysis parameters must be positive integers")
        values = dict(self.parameters)
        if not 1_000 <= values["target_rate"] <= 8_000:
            raise ValueError("target_rate is outside the safe baseline range")
        if (
            not 512 <= values["window_frames"] <= 8_192
            or values["window_frames"] & (values["window_frames"] - 1)
        ):
            raise ValueError("window_frames must be a bounded power of two")
        if not 128 <= values["hop_frames"] <= values["window_frames"]:
            raise ValueError("hop_frames is outside the safe baseline range")
        if not 40 <= values["min_bpm"] < values["max_bpm"] <= 240:
            raise ValueError("tempo bounds are invalid")
        if type(self.random_seed) is not int or self.random_seed != 0:
            raise ValueError("baseline-v1 is deterministic and requires seed 0")

    @classmethod
    def baseline(cls) -> AnalysisConfig:
        return cls(
            engine=EngineRef(),
            parameters=(
                ("hop_frames", 1024),
                ("max_bpm", 180),
                ("min_bpm", 60),
                ("target_rate", 2048),
                ("window_frames", 2048),
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "engine": self.engine.to_mapping(),
            "parameters": {name: value for name, value in self.parameters},
            "random_seed": self.random_seed,
            "determinism": "deterministic",
        }


@dataclass(frozen=True)
class AnalysisSpec:
    id: str
    asset_id: str
    timebase: Timebase
    analyzed_range: FrameRange
    config: AnalysisConfig

    def __post_init__(self) -> None:
        if not self.id.startswith("sha256:") or _DIGEST_RE.fullmatch(self.id[7:]) is None:
            raise ValueError("AnalysisSpec.id is invalid")
        if not self.asset_id.startswith("sha256:") or _DIGEST_RE.fullmatch(self.asset_id[7:]) is None:
            raise ValueError("AnalysisSpec.asset_id is invalid")
        self.timebase.validate_range(self.analyzed_range)
        if self.id != f"sha256:{content_digest(self.identity_mapping())}":
            raise ValueError("AnalysisSpec.id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        asset_id: str,
        timebase: Timebase,
        analyzed_range: FrameRange,
        config: AnalysisConfig | None = None,
    ) -> AnalysisSpec:
        selected = config or AnalysisConfig.baseline()
        identity = {
            "analysis_spec_schema_version": "1.0.0-draft",
            "asset_id": asset_id,
            "timebase": timebase.to_mapping(),
            "analyzed_range": analyzed_range.to_mapping(),
            "config": selected.to_mapping(),
        }
        return cls(
            id=f"sha256:{content_digest(identity)}",
            asset_id=asset_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            config=selected,
        )

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "analysis_spec_schema_version": "1.0.0-draft",
            "asset_id": self.asset_id,
            "timebase": self.timebase.to_mapping(),
            "analyzed_range": self.analyzed_range.to_mapping(),
            "config": self.config.to_mapping(),
        }


@dataclass(frozen=True)
class ChordCandidate:
    raw_label: str
    canonical_symbol: str | None
    rank: int
    confidence_ppm: int
    normalization: str = "exact"
    normalization_note: str = ""

    def __post_init__(self) -> None:
        if type(self.raw_label) is not str or not self.raw_label or len(self.raw_label) > 64:
            raise ValueError("candidate raw label is invalid")
        if self.canonical_symbol is not None and (
            type(self.canonical_symbol) is not str
            or _SYMBOL_RE.fullmatch(self.canonical_symbol) is None
        ):
            raise ValueError("candidate canonical symbol is outside the baseline vocabulary")
        if (
            type(self.rank) is not int
            or type(self.confidence_ppm) is not int
            or self.rank <= 0
            or not 0 <= self.confidence_ppm <= 1_000_000
        ):
            raise ValueError("candidate rank or confidence is invalid")
        if type(self.normalization) is not str or self.normalization not in {"exact", "unmapped"}:
            raise ValueError("candidate normalization is invalid")
        if type(self.normalization_note) is not str or len(self.normalization_note) > 160:
            raise ValueError("candidate normalization note is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "raw_label": self.raw_label,
            "canonical_symbol": self.canonical_symbol,
            "rank": self.rank,
            "confidence_ppm": self.confidence_ppm,
            "normalization": self.normalization,
            "normalization_note": self.normalization_note,
        }


@dataclass(frozen=True)
class ChordSegment:
    ordinal: int
    frame_range: FrameRange
    state: str
    candidates: tuple[ChordCandidate, ...]
    no_chord_probability_ppm: int

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or type(self.state) is not str
            or self.ordinal < 0
            or self.state not in {"chord", "no_chord", "unknown"}
        ):
            raise ValueError("segment ordinal or state is invalid")
        ranks = [candidate.rank for candidate in self.candidates]
        if len(self.candidates) > 8:
            raise ValueError("segment has too many chord candidates")
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("candidate ranks must be unique and contiguous")
        if self.state == "chord" and not self.candidates:
            raise ValueError("chord segment requires candidates")
        if self.state == "chord" and any(
            candidate.canonical_symbol == "N.C." for candidate in self.candidates
        ):
            raise ValueError("chord segment cannot contain a no-chord candidate")
        if self.state == "no_chord" and (
            not self.candidates
            or any(
                candidate.canonical_symbol != "N.C."
                for candidate in self.candidates
            )
        ):
            raise ValueError("no-chord segment requires no-chord candidates")
        if (
            type(self.no_chord_probability_ppm) is not int
            or not 0 <= self.no_chord_probability_ppm <= 1_000_000
        ):
            raise ValueError("no-chord probability is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "range": self.frame_range.to_mapping(),
            "state": self.state,
            "candidates": [candidate.to_mapping() for candidate in self.candidates],
            "no_chord_probability_ppm": self.no_chord_probability_ppm,
        }


@dataclass(frozen=True)
class TempoHypothesis:
    bpm_milli: int
    confidence_ppm: int

    def __post_init__(self) -> None:
        if type(self.bpm_milli) is not int or not 20_000 <= self.bpm_milli <= 400_000:
            raise ValueError("tempo is outside the supported domain")
        if (
            type(self.confidence_ppm) is not int
            or not 0 <= self.confidence_ppm <= 1_000_000
        ):
            raise ValueError("tempo confidence is invalid")

    def to_mapping(self) -> dict[str, int]:
        return {"bpm_milli": self.bpm_milli, "confidence_ppm": self.confidence_ppm}


@dataclass(frozen=True)
class KeyHypothesis:
    label: str
    confidence_ppm: int

    def __post_init__(self) -> None:
        if type(self.label) is not str or not self.label or len(self.label) > 32:
            raise ValueError("key label is invalid")
        if (
            type(self.confidence_ppm) is not int
            or not 0 <= self.confidence_ppm <= 1_000_000
        ):
            raise ValueError("key confidence is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {"label": self.label, "confidence_ppm": self.confidence_ppm}


@dataclass(frozen=True)
class ChordCandidateTimeline:
    id: str
    spec_id: str
    timebase: Timebase
    analyzed_range: FrameRange
    result_kind: str
    beats: tuple[int, ...]
    tempo_hypotheses: tuple[TempoHypothesis, ...]
    key_hypotheses: tuple[KeyHypothesis, ...]
    segments: tuple[ChordSegment, ...]
    engine: EngineRef

    def __post_init__(self) -> None:
        if not self.id.startswith("sha256:") or _DIGEST_RE.fullmatch(self.id[7:]) is None:
            raise ValueError("timeline id is invalid")
        if not self.spec_id.startswith("sha256:") or _DIGEST_RE.fullmatch(self.spec_id[7:]) is None:
            raise ValueError("timeline spec id is invalid")
        self.timebase.validate_range(self.analyzed_range)
        if self.result_kind not in {"candidates", "no_candidates"}:
            raise ValueError("timeline result kind is invalid")
        if len(self.beats) > 100_000:
            raise ValueError("timeline has too many beats")
        if len(self.tempo_hypotheses) > 8 or len(self.key_hypotheses) > 8:
            raise ValueError("timeline has too many global hypotheses")
        if len(self.segments) > 50_000:
            raise ValueError("timeline has too many segments")
        if any(type(beat) is not int for beat in self.beats):
            raise ValueError("beats must be integers")
        if tuple(sorted(set(self.beats))) != self.beats:
            raise ValueError("beats must be unique and ordered")
        if any(not (self.analyzed_range.start_frame <= beat < self.analyzed_range.end_frame) for beat in self.beats):
            raise ValueError("beat lies outside analyzed range")
        expected = self.analyzed_range.start_frame
        for ordinal, segment in enumerate(self.segments):
            if segment.ordinal != ordinal or segment.frame_range.start_frame != expected:
                raise ValueError("segments must be ordinal, ordered, and gap-explicit")
            expected = segment.frame_range.end_frame
        if self.segments and expected != self.analyzed_range.end_frame:
            raise ValueError("segments must cover the analyzed range")
        if self.result_kind == "candidates" and not self.segments:
            raise ValueError("candidate result requires segments")
        if self.result_kind == "no_candidates" and self.segments:
            raise ValueError("no-candidate result cannot contain segments")
        if self.id != f"sha256:{content_digest(self.payload_mapping())}":
            raise ValueError("timeline id does not match its content")

    @classmethod
    def create(
        cls,
        *,
        spec_id: str,
        timebase: Timebase,
        analyzed_range: FrameRange,
        result_kind: str,
        beats: tuple[int, ...],
        tempo_hypotheses: tuple[TempoHypothesis, ...],
        key_hypotheses: tuple[KeyHypothesis, ...],
        segments: tuple[ChordSegment, ...],
        engine: EngineRef,
    ) -> ChordCandidateTimeline:
        payload = _timeline_payload(
            spec_id=spec_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            result_kind=result_kind,
            beats=beats,
            tempo_hypotheses=tempo_hypotheses,
            key_hypotheses=key_hypotheses,
            segments=segments,
            engine=engine,
        )
        return cls(
            id=f"sha256:{content_digest(payload)}",
            spec_id=spec_id,
            timebase=timebase,
            analyzed_range=analyzed_range,
            result_kind=result_kind,
            beats=beats,
            tempo_hypotheses=tempo_hypotheses,
            key_hypotheses=key_hypotheses,
            segments=segments,
            engine=engine,
        )

    def payload_mapping(self) -> dict[str, Any]:
        return _timeline_payload(
            spec_id=self.spec_id,
            timebase=self.timebase,
            analyzed_range=self.analyzed_range,
            result_kind=self.result_kind,
            beats=self.beats,
            tempo_hypotheses=self.tempo_hypotheses,
            key_hypotheses=self.key_hypotheses,
            segments=self.segments,
            engine=self.engine,
        )

    def to_record_mapping(self) -> dict[str, Any]:
        return {"id": self.id, **self.payload_mapping()}

    def to_public_mapping(self) -> dict[str, Any]:
        value = self.payload_mapping()
        value.pop("spec_id")
        return value


@dataclass(frozen=True)
class AnalysisRun:
    id: str
    spec_id: str
    source_id: str
    created_at: str
    retry_of: str | None = None

    @classmethod
    def create(cls, *, spec_id: str, source_id: str, created_at: str, retry_of: str | None = None) -> AnalysisRun:
        return cls(f"run_{secrets.token_hex(16)}", spec_id, source_id, created_at, retry_of)

    def __post_init__(self) -> None:
        if type(self.id) is not str or _RUN_RE.fullmatch(self.id) is None:
            raise ValueError("run id is invalid")
        if (
            type(self.spec_id) is not str
            or not self.spec_id.startswith("sha256:")
            or _DIGEST_RE.fullmatch(self.spec_id[7:]) is None
        ):
            raise ValueError("run spec id is invalid")
        if type(self.source_id) is not str or _SOURCE_RE.fullmatch(self.source_id) is None:
            raise ValueError("run source id is invalid")
        if type(self.created_at) is not str or not self.created_at:
            raise ValueError("run creation time is invalid")
        if self.retry_of is not None and (
            type(self.retry_of) is not str or _RUN_RE.fullmatch(self.retry_of) is None
        ):
            raise ValueError("retry_of is invalid")

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "analysis_run_schema_version": "1.0.0-draft",
            "id": self.id,
            "spec_id": self.spec_id,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "retry_of": self.retry_of,
        }


@dataclass(frozen=True)
class RunState:
    run_id: str
    revision: int
    status: str
    updated_at: str
    failure_code: str | None = None
    failure_message: str | None = None
    retryable: bool = False
    timeline_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or _RUN_RE.fullmatch(self.run_id) is None
            or type(self.revision) is not int
            or self.revision < 0
        ):
            raise ValueError("run state identity is invalid")
        if type(self.status) is not str or self.status not in {
            "queued",
            "running",
            "cancel_requested",
            "cancelled",
            "failed",
            "succeeded",
        }:
            raise ValueError("run status is invalid")
        if type(self.updated_at) is not str or not self.updated_at:
            raise ValueError("run update time is invalid")
        if self.failure_code is not None and type(self.failure_code) is not str:
            raise ValueError("run failure code is invalid")
        if self.failure_message is not None and type(self.failure_message) is not str:
            raise ValueError("run failure message is invalid")
        if type(self.retryable) is not bool:
            raise ValueError("run retryable flag is invalid")
        if self.timeline_id is not None and (
            type(self.timeline_id) is not str
            or not self.timeline_id.startswith("sha256:")
            or _DIGEST_RE.fullmatch(self.timeline_id[7:]) is None
        ):
            raise ValueError("run timeline id is invalid")
        if self.status == "succeeded" and self.timeline_id is None:
            raise ValueError("successful run requires timeline")
        if self.status != "succeeded" and self.timeline_id is not None:
            raise ValueError("non-successful run cannot claim timeline")
        if self.status == "failed" and not self.failure_code:
            raise ValueError("failed run requires a failure code")

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "run_state_schema_version": "1.0.0-draft",
            "run_id": self.run_id,
            "revision": self.revision,
            "status": self.status,
            "updated_at": self.updated_at,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "retryable": self.retryable,
            "timeline_id": self.timeline_id,
        }

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "revision": self.revision,
            "status": self.status,
            "updated_at": self.updated_at,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "retryable": self.retryable,
        }


VALID_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"cancel_requested", "failed", "succeeded"},
    "cancel_requested": {"cancelled", "failed"},
    "cancelled": set(),
    "failed": set(),
    "succeeded": set(),
}


def _timeline_payload(
    *,
    spec_id: str,
    timebase: Timebase,
    analyzed_range: FrameRange,
    result_kind: str,
    beats: tuple[int, ...],
    tempo_hypotheses: tuple[TempoHypothesis, ...],
    key_hypotheses: tuple[KeyHypothesis, ...],
    segments: tuple[ChordSegment, ...],
    engine: EngineRef,
) -> dict[str, Any]:
    return {
        "candidate_timeline_schema_version": "1.0.0-draft",
        "spec_id": spec_id,
        "timebase": timebase.to_mapping(),
        "analyzed_range": analyzed_range.to_mapping(),
        "result_kind": result_kind,
        "beats": list(beats),
        "tempo_hypotheses": [item.to_mapping() for item in tempo_hypotheses],
        "key_hypotheses": [item.to_mapping() for item in key_hypotheses],
        "segments": [segment.to_mapping() for segment in segments],
        "engine": engine.to_mapping(),
    }


def engine_from_mapping(value: dict[str, Any]) -> EngineRef:
    _require_keys(
        value,
        {
            "engine_id",
            "engine_version",
            "model_name",
            "model_version",
            "vocabulary_id",
            "vocabulary_version",
        },
        "analysis engine",
    )
    return EngineRef(
        engine_id=_mapping_str(value["engine_id"]),
        engine_version=_mapping_str(value["engine_version"]),
        model_name=_mapping_str(value["model_name"]),
        model_version=_mapping_str(value["model_version"]),
        vocabulary_id=_mapping_str(value["vocabulary_id"]),
        vocabulary_version=_mapping_str(value["vocabulary_version"]),
    )


def config_from_mapping(value: dict[str, Any]) -> AnalysisConfig:
    _require_keys(
        value,
        {"engine", "parameters", "random_seed", "determinism"},
        "analysis config",
    )
    if _mapping_str(value["determinism"]) != "deterministic":
        raise ValueError("analysis determinism marker is invalid")
    parameters = value["parameters"]
    if not isinstance(parameters, dict):
        raise ValueError("analysis parameters must be a mapping")
    return AnalysisConfig(
        engine=engine_from_mapping(value["engine"]),
        parameters=tuple(
            sorted((_mapping_str(name), _mapping_int(item)) for name, item in parameters.items())
        ),
        random_seed=_mapping_int(value["random_seed"]),
    )


def spec_from_mapping(value: dict[str, Any]) -> AnalysisSpec:
    _require_keys(
        value,
        {
            "analysis_spec_schema_version",
            "id",
            "asset_id",
            "timebase",
            "analyzed_range",
            "config",
        },
        "analysis spec",
    )
    _require_version(
        value,
        "analysis_spec_schema_version",
        "1.0.0-draft",
        "analysis spec",
    )
    timebase = value["timebase"]
    frame_range = value["analyzed_range"]
    _require_keys(
        timebase,
        {"sample_rate", "duration_frames", "unit"},
        "analysis timebase",
    )
    if _mapping_str(timebase["unit"]) != "sample_frame":
        raise ValueError("analysis timebase unit is invalid")
    _require_keys(
        frame_range,
        {"start_frame", "end_frame"},
        "analysis frame range",
    )
    return AnalysisSpec(
        id=_mapping_str(value["id"]),
        asset_id=_mapping_str(value["asset_id"]),
        timebase=Timebase(
            _mapping_int(timebase["sample_rate"]),
            _mapping_int(timebase["duration_frames"]),
        ),
        analyzed_range=FrameRange(
            _mapping_int(frame_range["start_frame"]),
            _mapping_int(frame_range["end_frame"]),
        ),
        config=config_from_mapping(value["config"]),
    )


def timeline_from_mapping(value: dict[str, Any]) -> ChordCandidateTimeline:
    _require_keys(
        value,
        {
            "candidate_timeline_schema_version",
            "id",
            "spec_id",
            "timebase",
            "analyzed_range",
            "result_kind",
            "beats",
            "tempo_hypotheses",
            "key_hypotheses",
            "segments",
            "engine",
        },
        "candidate timeline",
    )
    _require_version(
        value,
        "candidate_timeline_schema_version",
        "1.0.0-draft",
        "candidate timeline",
    )
    timebase = value["timebase"]
    analyzed = value["analyzed_range"]
    _require_keys(
        timebase,
        {"sample_rate", "duration_frames", "unit"},
        "timeline timebase",
    )
    if _mapping_str(timebase["unit"]) != "sample_frame":
        raise ValueError("timeline timebase unit is invalid")
    _require_keys(analyzed, {"start_frame", "end_frame"}, "timeline analyzed range")
    beats = _mapping_list(value["beats"], "timeline beats")
    tempos = _mapping_list(value["tempo_hypotheses"], "tempo hypotheses")
    keys = _mapping_list(value["key_hypotheses"], "key hypotheses")
    raw_segments = _mapping_list(value["segments"], "timeline segments")
    segments = []
    for item in raw_segments:
        _require_keys(
            item,
            {"ordinal", "range", "state", "candidates", "no_chord_probability_ppm"},
            "chord segment",
        )
        frame_range = item["range"]
        _require_keys(frame_range, {"start_frame", "end_frame"}, "segment frame range")
        raw_candidates = _mapping_list(item["candidates"], "segment candidates")
        candidates = tuple(
            _candidate_from_mapping(candidate) for candidate in raw_candidates
        )
        segments.append(
            ChordSegment(
                ordinal=_mapping_int(item["ordinal"]),
                frame_range=FrameRange(
                    _mapping_int(frame_range["start_frame"]),
                    _mapping_int(frame_range["end_frame"]),
                ),
                state=_mapping_str(item["state"]),
                candidates=candidates,
                no_chord_probability_ppm=_mapping_int(item["no_chord_probability_ppm"]),
            )
        )
    return ChordCandidateTimeline(
        id=_mapping_str(value["id"]),
        spec_id=_mapping_str(value["spec_id"]),
        timebase=Timebase(
            _mapping_int(timebase["sample_rate"]),
            _mapping_int(timebase["duration_frames"]),
        ),
        analyzed_range=FrameRange(
            _mapping_int(analyzed["start_frame"]),
            _mapping_int(analyzed["end_frame"]),
        ),
        result_kind=_mapping_str(value["result_kind"]),
        beats=tuple(_mapping_int(item) for item in beats),
        tempo_hypotheses=tuple(
            _tempo_from_mapping(item) for item in tempos
        ),
        key_hypotheses=tuple(
            _key_from_mapping(item) for item in keys
        ),
        segments=tuple(segments),
        engine=engine_from_mapping(value["engine"]),
    )


def run_from_mapping(value: dict[str, Any]) -> AnalysisRun:
    _require_keys(
        value,
        {
            "analysis_run_schema_version",
            "id",
            "spec_id",
            "source_id",
            "created_at",
            "retry_of",
        },
        "analysis run",
    )
    _require_version(
        value,
        "analysis_run_schema_version",
        "1.0.0-draft",
        "analysis run",
    )
    return AnalysisRun(
        id=_mapping_str(value["id"]),
        spec_id=_mapping_str(value["spec_id"]),
        source_id=_mapping_str(value["source_id"]),
        created_at=_mapping_str(value["created_at"]),
        retry_of=_mapping_optional_str(value["retry_of"]),
    )


def state_from_mapping(value: dict[str, Any]) -> RunState:
    _require_keys(
        value,
        {
            "run_state_schema_version",
            "run_id",
            "revision",
            "status",
            "updated_at",
            "failure_code",
            "failure_message",
            "retryable",
            "timeline_id",
        },
        "analysis run state",
    )
    _require_version(
        value,
        "run_state_schema_version",
        "1.0.0-draft",
        "analysis run state",
    )
    return RunState(
        run_id=_mapping_str(value["run_id"]),
        revision=_mapping_int(value["revision"]),
        status=_mapping_str(value["status"]),
        updated_at=_mapping_str(value["updated_at"]),
        failure_code=_mapping_optional_str(value["failure_code"]),
        failure_message=_mapping_optional_str(value["failure_message"]),
        retryable=_mapping_bool(value["retryable"]),
        timeline_id=_mapping_optional_str(value["timeline_id"]),
    )


def _candidate_from_mapping(value: Any) -> ChordCandidate:
    _require_keys(
        value,
        {
            "raw_label",
            "canonical_symbol",
            "rank",
            "confidence_ppm",
            "normalization",
            "normalization_note",
        },
        "chord candidate",
    )
    return ChordCandidate(
        raw_label=_mapping_str(value["raw_label"]),
        canonical_symbol=_mapping_optional_str(value["canonical_symbol"]),
        rank=_mapping_int(value["rank"]),
        confidence_ppm=_mapping_int(value["confidence_ppm"]),
        normalization=_mapping_str(value["normalization"]),
        normalization_note=_mapping_str(value["normalization_note"]),
    )


def _tempo_from_mapping(value: Any) -> TempoHypothesis:
    _require_keys(value, {"bpm_milli", "confidence_ppm"}, "tempo hypothesis")
    return TempoHypothesis(
        _mapping_int(value["bpm_milli"]),
        _mapping_int(value["confidence_ppm"]),
    )


def _key_from_mapping(value: Any) -> KeyHypothesis:
    _require_keys(value, {"label", "confidence_ppm"}, "key hypothesis")
    return KeyHypothesis(
        _mapping_str(value["label"]),
        _mapping_int(value["confidence_ppm"]),
    )


def _mapping_int(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("analysis record integer is invalid")
    return value


def _mapping_str(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("analysis record string is invalid")
    return value


def _mapping_bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("analysis record boolean is invalid")
    return value


def _mapping_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return _mapping_str(value)


def _mapping_list(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{name} must be a list")
    return value


def _require_keys(value: Any, expected: set[str], name: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} keys are invalid")


def _require_version(
    value: dict[str, Any],
    key: str,
    expected: str,
    name: str,
) -> None:
    if _mapping_str(value[key]) != expected:
        raise ValueError(f"{name} schema version is unsupported")
