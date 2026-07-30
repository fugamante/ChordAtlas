from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from chordatlas.provenance import (
    ClaimOrigin,
    Confidence,
    ProvenanceRecord,
    format_confidence,
    parse_provenance,
    provenance_to_mapping,
    provenance_warnings,
)

SCHEMA_VERSION = "1.0.0"
_MAX_ANALYSIS_DEPTH = 256


def _require_six_string_values(
    value: Any,
    *,
    field: str,
    optional: bool = False,
) -> None:
    valid = (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) == 6
        and all(isinstance(item, str) for item in value)
    )
    if not valid:
        suffix = " when provided" if optional else ""
        raise ValueError(f"{field} must define exactly six strings{suffix}")


@dataclass(frozen=True)
class ChordShape:
    """A playable six-string guitar chord shape."""

    name: str
    frets: tuple[str, str, str, str, str, str]
    fingers: tuple[str, str, str, str, str, str] | None = None
    tuning: tuple[str, str, str, str, str, str] = ("E", "A", "D", "G", "B", "e")
    notes: str | None = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_six_string_values(self.frets, field="ChordShape.frets")
        _require_six_string_values(self.tuning, field="ChordShape.tuning")
        if self.fingers is not None:
            _require_six_string_values(
                self.fingers,
                field="ChordShape.fingers",
                optional=True,
            )

    @classmethod
    def from_mapping(cls, name: str, value: dict[str, Any]) -> ChordShape:
        value = _require_mapping(value, field="Chord shapes")
        if "frets" not in value:
            raise ValueError("ChordShape.frets is required")
        frets = _string_sequence(value["frets"], field="ChordShape.frets")
        fingers_value = value.get("fingers")
        fingers = (
            _string_sequence(fingers_value, field="ChordShape.fingers")
            if fingers_value is not None
            else None
        )
        tuning_value = value.get("tuning", ("E", "A", "D", "G", "B", "e"))
        tuning = _string_sequence(tuning_value, field="ChordShape.tuning")
        _require_six_string_values(frets, field="ChordShape.frets")
        _require_six_string_values(tuning, field="ChordShape.tuning")
        if fingers is not None:
            _require_six_string_values(
                fingers,
                field="ChordShape.fingers",
                optional=True,
            )
        return cls(
            name=name,
            frets=frets,  # type: ignore[arg-type]
            fingers=fingers,  # type: ignore[arg-type]
            tuning=tuning,  # type: ignore[arg-type]
            notes=value.get("notes"),
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "frets": list(self.frets),
            "tuning": list(self.tuning),
        }
        if self.fingers:
            value["fingers"] = list(self.fingers)
        if self.notes:
            value["notes"] = self.notes
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class ChartMeasure:
    """A measure containing chord symbols and optional timing/provenance."""

    chords: tuple[str, ...]
    timestamp: str | None = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __iter__(self):
        return iter(self.chords)

    @classmethod
    def from_value(cls, value: Any) -> ChartMeasure:
        if isinstance(value, Mapping):
            if "chords" not in value:
                raise ValueError("Chart measure mappings must include chords")
            raw_chords = value["chords"]
            return cls(
                chords=_string_sequence(raw_chords, field="Chart measure chords"),
                timestamp=_optional_str(value.get("timestamp")),
                provenance=parse_provenance(value.get("provenance")),
            )
        return cls(chords=_string_sequence(value, field="Chart measure chords"))

    def to_mapping(self) -> dict[str, Any] | list[str]:
        if not self.timestamp and not self.provenance:
            return list(self.chords)
        value: dict[str, Any] = {"chords": list(self.chords)}
        if self.timestamp:
            value["timestamp"] = self.timestamp
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class ChartSection:
    """A named chart section made of chord bars and optional chart notes."""

    name: str
    bars: tuple[ChartMeasure, ...]
    repeat: str | None = None
    notes: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.name, field="ChartSection.name")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ChartSection:
        value = _require_mapping(value, field="Chart sections")
        if "name" not in value:
            raise ValueError("Chart sections must include a name")
        raw_bars = _require_sequence(value.get("bars", []), field="Chart section bars")
        bars = tuple(ChartMeasure.from_value(bar) for bar in raw_bars)
        notes = _string_sequence(value.get("notes", ()), field="Chart section notes")
        return cls(
            name=str(value["name"]),
            bars=bars,
            repeat=_optional_str(value.get("repeat")),
            notes=notes,
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "bars": [bar.to_mapping() for bar in self.bars],
        }
        if self.repeat:
            value["repeat"] = self.repeat
        if self.notes:
            value["notes"] = list(self.notes)
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class VersionEntry:
    """A version-history entry for a chart revision."""

    version: str
    changes: tuple[str, ...]
    provenance: tuple[ProvenanceRecord, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> VersionEntry:
        value = _require_mapping(value, field="Version history entries")
        if "version" not in value:
            raise ValueError("Version history entries must include a version")
        if "changes" not in value:
            changes = ()
        elif isinstance(raw_changes := value["changes"], str):
            changes = (raw_changes,)
        elif isinstance(raw_changes, Sequence):
            changes = tuple(str(change) for change in raw_changes)
        else:
            raise ValueError("Version history changes must be a string or list")
        return cls(
            version=str(value["version"]),
            changes=changes,
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {"version": self.version, "changes": list(self.changes)}
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class RecordingSource:
    """A recording, source, take, stem, or release version that chart claims can reference."""

    id: str
    title: str
    source_url: str | None = None
    version_label: str | None = None
    notes: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.id, field="RecordingSource.id")
        _require_nonempty(self.title, field="RecordingSource.title")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RecordingSource:
        value = _require_mapping(value, field="Recording source entries")
        if "id" not in value:
            raise ValueError("Recording source entries must include an id")
        title = value.get("title", value["id"])
        return cls(
            id=str(value["id"]),
            title=str(title),
            source_url=_optional_str(value.get("source_url")),
            version_label=_optional_str(value.get("version_label")),
            notes=_parse_string_tuple(value.get("notes", ()), claim="recording source notes"),
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
        }
        if self.source_url:
            value["source_url"] = self.source_url
        if self.version_label:
            value["version_label"] = self.version_label
        if self.notes:
            value["notes"] = list(self.notes)
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class RecordingNote:
    """A single recording claim with optional certainty and evidence metadata."""

    text: str
    confidence: Confidence | None = None
    claim_origin: ClaimOrigin | None = None
    category: str | None = None
    severity: str | None = None
    recording_ids: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.text, field="RecordingNote.text")

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        path: str = "structured_recording_note",
    ) -> RecordingNote:
        if isinstance(value, Mapping):
            if "text" not in value:
                raise ValueError("Structured recording note entries must include text")
            text = str(value["text"])
            _require_nonempty(text, field="RecordingNote.text")
            recording_ids = _parse_recording_ids(value, path=path)
            return cls(
                text=text,
                confidence=_parse_confidence(value.get("confidence")),
                claim_origin=_parse_claim_origin(value.get("claim_origin")),
                category=_optional_str(value.get("category")),
                severity=_optional_str(value.get("severity")),
                recording_ids=recording_ids,
                provenance=parse_provenance(value.get("provenance")),
            )
        return cls(text=str(value))

    @property
    def has_metadata(self) -> bool:
        return bool(
            self.confidence
            or self.claim_origin
            or self.category
            or self.severity
            or self.recording_ids
            or self.provenance
        )

    def to_mapping(self) -> dict[str, Any] | str:
        if not self.has_metadata:
            return self.text
        value: dict[str, Any] = {"text": self.text}
        if self.confidence:
            value["confidence"] = self.confidence.value
        if self.claim_origin:
            value["claim_origin"] = self.claim_origin.value
        if self.category:
            value["category"] = self.category
        if self.severity:
            value["severity"] = self.severity
        if self.recording_ids:
            value["recording_ids"] = list(self.recording_ids)
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class RecordingNoteGroup:
    """A grouped set of recording notes, such as Guitar 1, Bass, or Effects."""

    name: str
    notes: tuple[RecordingNote, ...] = ()
    value: RecordingNote | None = None
    category: str | None = None
    severity: str | None = None
    recording_ids: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.name, field="RecordingNoteGroup.name")
        if not self.notes and self.value is None:
            raise ValueError("Structured recording note groups must include notes or value")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RecordingNoteGroup:
        value = _require_mapping(value, field="Structured recording note groups")
        if "name" not in value:
            raise ValueError("Structured recording note groups must include a name")
        name = str(value["name"])
        _require_nonempty(name, field="RecordingNoteGroup.name")
        path = f"structured_recording_notes[{name!r}]"
        recording_ids = _parse_recording_ids(value, path=path)
        notes = _parse_recording_notes(value.get("notes", ()), path=f"{path}.notes")
        group_value = value.get("value")
        if group_value is not None:
            group_value = RecordingNote.from_value(group_value, path=f"{path}.value")
        if not notes and group_value is None:
            raise ValueError("Structured recording note groups must include notes or value")
        return cls(
            name=name,
            notes=notes,
            value=group_value,
            category=_optional_str(value.get("category")),
            severity=_optional_str(value.get("severity")),
            recording_ids=recording_ids,
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name}
        if self.notes:
            value["notes"] = [note.to_mapping() for note in self.notes]
        if self.value is not None:
            value["value"] = self.value.to_mapping()
        if self.category:
            value["category"] = self.category
        if self.severity:
            value["severity"] = self.severity
        if self.recording_ids:
            value["recording_ids"] = list(self.recording_ids)
        if self.provenance:
            value["provenance"] = provenance_to_mapping(self.provenance)
        return value


@dataclass(frozen=True)
class SongChart:
    """Structured song chart data independent of any export format."""

    title: str
    artist: str | None = None
    key: str | None = None
    tuning: str = "Standard"
    capo: str = "None"
    version: str = "1.0"
    confidence: Confidence | None = None
    sections: tuple[ChartSection, ...] = ()
    voicing_notes: tuple[str, ...] = ()
    version_history: tuple[VersionEntry, ...] = ()
    analysis: dict[str, Any] = field(default_factory=dict)
    performance_notes: tuple[str, ...] = ()
    recording_notes: tuple[str, ...] = ()
    recordings: tuple[RecordingSource, ...] = ()
    structured_recording_notes: tuple[RecordingNoteGroup, ...] = ()
    source: str | None = None
    schema_version: str = SCHEMA_VERSION
    provenance: tuple[ProvenanceRecord, ...] = ()
    metadata_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    chord_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    analysis_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    performance_note_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(
        default_factory=dict
    )
    recording_note_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.title, field="SongChart.title")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        _validate_unique_recording_ids(self.recordings)
        _validate_recording_references(self.recordings, self.structured_recording_notes)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SongChart:
        value = _require_mapping(value, field="Song charts")
        if "title" not in value:
            raise ValueError("Song charts must include a title")
        raw_sections = _require_sequence(value.get("sections", ()), field="sections")
        sections = tuple(ChartSection.from_mapping(section) for section in raw_sections)
        raw_history = _require_sequence(
            value.get("version_history", ()),
            field="version_history",
            error="version_history must be a list of entries",
        )
        version_history = tuple(VersionEntry.from_mapping(entry) for entry in raw_history)
        analysis = _parse_analysis(value.get("analysis", {}))
        return cls(
            title=str(value["title"]),
            artist=_optional_str(value.get("artist")),
            key=_optional_str(value.get("key")),
            tuning=str(value.get("tuning", "Standard")),
            capo=str(value.get("capo", "None")),
            version=str(value.get("version", "1.0")),
            confidence=_parse_confidence(value.get("confidence")),
            sections=sections,
            voicing_notes=_string_sequence(value.get("voicing_notes", ()), field="voicing_notes"),
            version_history=version_history,
            analysis=analysis,
            performance_notes=_string_sequence(
                value.get("performance_notes", ()), field="performance_notes"
            ),
            recording_notes=_string_sequence(
                value.get("recording_notes", ()), field="recording_notes"
            ),
            recordings=_parse_recording_sources(value.get("recordings", ())),
            structured_recording_notes=_parse_recording_groups(
                value.get("structured_recording_notes", ())
            ),
            source=_optional_str(value.get("source")),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
            provenance=parse_provenance(value.get("provenance")),
            metadata_provenance=_parse_provenance_map(
                value.get("metadata_provenance"),
                field="metadata_provenance",
            ),
            chord_provenance=_parse_provenance_map(
                value.get("chord_provenance"),
                field="chord_provenance",
            ),
            analysis_provenance=_parse_provenance_map(
                value.get("analysis_provenance"),
                field="analysis_provenance",
            ),
            performance_note_provenance=_parse_provenance_map(
                value.get("performance_note_provenance"),
                field="performance_note_provenance",
            ),
            recording_note_provenance=_parse_provenance_map(
                value.get("recording_note_provenance"),
                field="recording_note_provenance",
            ),
        )

    @property
    def used_chords(self) -> tuple[str, ...]:
        chords: list[str] = []
        seen: set[str] = set()
        for section in self.sections:
            for bar in section.bars:
                for chord in bar:
                    if chord not in seen:
                        chords.append(chord)
                        seen.add(chord)
        return tuple(chords)

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "title": self.title,
            "tuning": self.tuning,
            "capo": self.capo,
            "version": self.version,
            "sections": [section.to_mapping() for section in self.sections],
        }
        optional_fields: tuple[tuple[str, Any], ...] = (
            ("artist", self.artist),
            ("key", self.key),
            ("confidence", self.confidence.value if self.confidence else None),
            ("source", self.source),
            ("voicing_notes", list(self.voicing_notes) if self.voicing_notes else None),
            (
                "version_history",
                [entry.to_mapping() for entry in self.version_history]
                if self.version_history
                else None,
            ),
            ("analysis", self.analysis if self.analysis else None),
            (
                "performance_notes",
                list(self.performance_notes) if self.performance_notes else None,
            ),
            ("recording_notes", list(self.recording_notes) if self.recording_notes else None),
            (
                "recordings",
                [recording.to_mapping() for recording in self.recordings]
                if self.recordings
                else None,
            ),
            (
                "structured_recording_notes",
                [group.to_mapping() for group in self.structured_recording_notes]
                if self.structured_recording_notes
                else None,
            ),
            ("provenance", provenance_to_mapping(self.provenance) if self.provenance else None),
            ("metadata_provenance", _provenance_map_to_mapping(self.metadata_provenance)),
            ("chord_provenance", _provenance_map_to_mapping(self.chord_provenance)),
            ("analysis_provenance", _provenance_map_to_mapping(self.analysis_provenance)),
            (
                "performance_note_provenance",
                _provenance_map_to_mapping(self.performance_note_provenance),
            ),
            (
                "recording_note_provenance",
                _provenance_map_to_mapping(self.recording_note_provenance),
            ),
        )
        for key, item in optional_fields:
            if item:
                value[key] = item
        return value

    def provenance_warnings(self) -> tuple[str, ...]:
        warnings: list[str] = []
        warnings.extend(provenance_warnings(self.provenance, claim="chart"))
        for field_name, records in self.metadata_provenance.items():
            warnings.extend(provenance_warnings(records, claim=f"metadata.{field_name}"))
        for chord, records in self.chord_provenance.items():
            warnings.extend(provenance_warnings(records, claim=f"chord.{chord}"))
        for field_name, records in self.analysis_provenance.items():
            warnings.extend(provenance_warnings(records, claim=f"analysis.{field_name}"))
        for note_key, records in self.performance_note_provenance.items():
            warnings.extend(
                provenance_warnings(records, claim=f"performance_note.{note_key}")
            )
        for note_key, records in self.recording_note_provenance.items():
            warnings.extend(provenance_warnings(records, claim=f"recording_note.{note_key}"))
        for index, recording in enumerate(self.recordings):
            warnings.extend(
                provenance_warnings(recording.provenance, claim=f"recording.{index}")
            )
        for index, group in enumerate(self.structured_recording_notes):
            warnings.extend(
                provenance_warnings(records=group.provenance, claim=f"recording_group.{index}")
            )
            for note_index, note in enumerate(group.notes):
                warnings.extend(
                    provenance_warnings(
                        note.provenance,
                        claim=f"recording_group.{index}.note.{note_index}",
                    )
                )
            if group.value is not None:
                warnings.extend(
                    provenance_warnings(
                        group.value.provenance,
                        claim=f"recording_group.{index}.value",
                    )
                )
        for index, section in enumerate(self.sections):
            warnings.extend(provenance_warnings(section.provenance, claim=f"section.{index}"))
            for measure_index, measure in enumerate(section.bars):
                warnings.extend(
                    provenance_warnings(
                        measure.provenance,
                        claim=f"section.{index}.measure.{measure_index}",
                    )
                )
        for index, entry in enumerate(self.version_history):
            warnings.extend(provenance_warnings(entry.provenance, claim=f"version.{index}"))
        return tuple(warnings)


def _parse_confidence(value: Any) -> Confidence | None:
    label = format_confidence(value)
    if label is None:
        return None
    return Confidence(label.lower())


def _parse_claim_origin(value: Any) -> ClaimOrigin | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for origin in ClaimOrigin:
        if normalized in {origin.value, origin.name.lower()}:
            return origin
    allowed = ", ".join(origin.value for origin in ClaimOrigin)
    raise ValueError(f"Invalid ClaimOrigin: {value!r}. Expected one of: {allowed}")


def _parse_provenance_map(
    value: Any,
    *,
    field: str,
) -> dict[str, tuple[ProvenanceRecord, ...]]:
    if value is None:
        return {}
    value = _require_mapping(value, field="Provenance maps")
    items = _canonical_mapping_items(value, field=field)
    return {canonical: parse_provenance(records) for canonical, records in items}


def _diagnostic_repr(value: Any, *, limit: int = 120) -> str:
    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<{type(value).__name__}>"
    escaped = rendered.encode("unicode_escape", errors="backslashreplace").decode("ascii")
    if len(escaped) > limit:
        return escaped[: limit - 3] + "..."
    return escaped


def _canonical_mapping_items(
    value: Mapping[Any, Any],
    *,
    field: str,
) -> list[tuple[str, Any]]:
    items = []
    authored_by_key: dict[str, Any] = {}
    for authored, item in value.items():
        try:
            canonical = str(authored)
        except Exception:
            raise ValueError(
                f"{field} key {_diagnostic_repr(authored)} cannot be normalized to text"
            ) from None
        if canonical in authored_by_key:
            first = authored_by_key[canonical]
            raise ValueError(
                f"{field} keys {_diagnostic_repr(first)} and "
                f"{_diagnostic_repr(authored)} both normalize to "
                f"{_diagnostic_repr(canonical)}"
            )
        authored_by_key[canonical] = authored
        items.append((canonical, item))
    return items


def _parse_recording_sources(value: Any) -> tuple[RecordingSource, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(
            RecordingSource.from_mapping(_recording_source_mapping(recording_id, source_value))
            for recording_id, source_value in _canonical_mapping_items(
                value,
                field="recordings",
            )
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(RecordingSource.from_mapping(source) for source in value)
    raise ValueError("recordings must be a list or mapping")


def _recording_source_mapping(recording_id: Any, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _compact_identity(
            value,
            identity="id",
            outer=recording_id,
            field="recordings",
        )
    return {"id": str(recording_id), "title": str(value)}


def _parse_recording_groups(value: Any) -> tuple[RecordingNoteGroup, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(
            RecordingNoteGroup.from_mapping(_recording_group_mapping(name, group_value))
            for name, group_value in _canonical_mapping_items(
                value,
                field="structured_recording_notes",
            )
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(RecordingNoteGroup.from_mapping(group) for group in value)
    raise ValueError("structured_recording_notes must be a list or mapping")


def _recording_group_mapping(name: Any, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _compact_identity(
            value,
            identity="name",
            outer=name,
            field="structured_recording_notes",
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {"name": str(name), "notes": list(value)}
    return {"name": str(name), "value": value}


def _compact_identity(
    value: Mapping[Any, Any],
    *,
    identity: str,
    outer: Any,
    field: str,
) -> dict[str, Any]:
    canonical = str(outer)
    if identity in value:
        nested = value[identity]
        if nested is None or str(nested) != canonical:
            raise ValueError(
                f"{field} mapping key {canonical!r} conflicts with nested "
                f"{identity} {nested!r}; remove the nested {identity} or use the explicit list form"
            )
    normalized = dict(value)
    normalized[identity] = canonical
    return normalized


def _parse_recording_notes(value: Any, *, path: str) -> tuple[RecordingNote, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str) or isinstance(value, Mapping):
        return (RecordingNote.from_value(value, path=f"{path}[0]"),)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(
            RecordingNote.from_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    raise ValueError("recording note group notes must be a string, mapping, or list")


def _parse_recording_ids(value: Mapping[str, Any], *, path: str) -> tuple[str, ...]:
    has_plural = "recording_ids" in value
    has_singular = "recording_id" in value
    if not has_plural and not has_singular:
        return ()

    if has_plural and has_singular:
        plural = value["recording_ids"]
        singular = value["recording_id"]
        try:
            plural_ids = _recording_id_tuple(plural)
            singular_ids = _recording_id_tuple(singular)
        except ValueError:
            raise _recording_alias_error(path, plural, singular) from None
        if plural_ids != singular_ids:
            raise _recording_alias_error(path, plural, singular)
        return plural_ids

    key = "recording_ids" if has_plural else "recording_id"
    raw_ids = value[key]
    try:
        return _recording_id_tuple(raw_ids)
    except ValueError:
        raise ValueError(
            f"{path}.{key}={raw_ids!r} must be a string or list; keep only recording_ids"
        ) from None


def _recording_id_tuple(raw_ids: Any) -> tuple[str, ...]:
    if isinstance(raw_ids, str):
        return (raw_ids,)
    if isinstance(raw_ids, Sequence):
        return tuple(str(recording_id) for recording_id in raw_ids)
    raise ValueError("recording scope must be a string or list")


def _recording_alias_error(path: str, plural: Any, singular: Any) -> ValueError:
    return ValueError(
        f"{path} has conflicting recording scope aliases: "
        f"recording_ids={plural!r}, recording_id={singular!r}; keep only recording_ids"
    )


def _parse_string_tuple(value: Any, *, claim: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_sequence(value, field=claim)


def _string_sequence(value: Any, *, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    items = _require_sequence(value, field=field)
    if any(isinstance(item, Mapping) for item in items):
        raise ValueError(f"{field} entries must not be mappings")
    return tuple(str(item) for item in items)


def _parse_analysis(value: Any) -> dict[str, Any]:
    analysis = _require_mapping(value, field="analysis")
    return _analysis_value(analysis, path="analysis", normalize_known=True)


def _analysis_list(value: Any, *, field: str) -> list[str | int | float | bool]:
    if isinstance(value, (str, int, float, bool)):
        return [value]
    if isinstance(value, (bytes, Mapping)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a scalar or list of scalars")
    items = list(value)
    if any(not isinstance(item, (str, int, float, bool)) for item in items):
        raise ValueError(f"{field} entries must be strings, numbers, or booleans")
    return items


def _analysis_value(
    value: Any,
    *,
    path: str,
    active: set[int] | None = None,
    normalize_known: bool = False,
    depth: int = 0,
) -> Any:
    if active is None:
        active = set()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if isinstance(value, Mapping):
        if depth >= _MAX_ANALYSIS_DEPTH:
            raise ValueError(
                f"analysis exceeds maximum nesting depth {_MAX_ANALYSIS_DEPTH}"
            )
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive container")
        active.add(identity)
        normalized: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"{path} keys must be strings")
                item_path = _analysis_path(path, key)
                if normalize_known and key in ("roman", "nashville"):
                    item = _analysis_list(item, field=item_path)
                normalized[key] = _analysis_value(
                    item,
                    path=item_path,
                    active=active,
                    depth=depth + 1,
                )
            return normalized
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if depth >= _MAX_ANALYSIS_DEPTH:
            raise ValueError(
                f"analysis exceeds maximum nesting depth {_MAX_ANALYSIS_DEPTH}"
            )
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive container")
        active.add(identity)
        try:
            return [
                _analysis_value(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                    depth=depth + 1,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _analysis_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _require_sequence(
    value: Any,
    *,
    field: str,
    error: str | None = None,
) -> Sequence[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise ValueError(error or f"{field} must be a list")
    return value


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _require_nonempty(value: str, *, field: str) -> None:
    if value == "":
        raise ValueError(f"{field} must not be empty")


def _validate_recording_references(
    recordings: tuple[RecordingSource, ...],
    groups: tuple[RecordingNoteGroup, ...],
) -> None:
    known_ids = {recording.id for recording in recordings}
    if not known_ids:
        if any(_group_recording_ids(group) for group in groups):
            raise ValueError("recording_ids require matching recordings entries")
        return
    for group in groups:
        _validate_recording_ids(group.recording_ids, known_ids)
        for note in group.notes:
            _validate_recording_ids(note.recording_ids, known_ids)
        if group.value is not None:
            _validate_recording_ids(group.value.recording_ids, known_ids)


def _validate_recording_ids(recording_ids: tuple[str, ...], known_ids: set[str]) -> None:
    unknown_ids = tuple(recording_id for recording_id in recording_ids if recording_id not in known_ids)
    if unknown_ids:
        known = ", ".join(sorted(known_ids))
        unknown = ", ".join(unknown_ids)
        raise ValueError(f"Unknown recording_id(s): {unknown}. Known recordings: {known}")


def _validate_unique_recording_ids(recordings: tuple[RecordingSource, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for recording in recordings:
        if recording.id in seen:
            duplicates.append(recording.id)
        seen.add(recording.id)
    if duplicates:
        raise ValueError(f"Duplicate recording id(s): {', '.join(duplicates)}")


def _group_recording_ids(group: RecordingNoteGroup) -> tuple[str, ...]:
    recording_ids = list(group.recording_ids)
    for note in group.notes:
        recording_ids.extend(note.recording_ids)
    if group.value is not None:
        recording_ids.extend(group.value.recording_ids)
    return tuple(recording_ids)


def _provenance_map_to_mapping(
    value: dict[str, tuple[ProvenanceRecord, ...]],
) -> dict[str, list[dict[str, Any]]] | None:
    if not value:
        return None
    return {key: provenance_to_mapping(records) for key, records in value.items()}
