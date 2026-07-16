from __future__ import annotations

from collections.abc import Sequence
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
        if len(self.frets) != 6:
            raise ValueError(f"ChordShape {self.name!r} must define exactly six strings")

    @classmethod
    def from_mapping(cls, name: str, value: dict[str, Any]) -> ChordShape:
        frets = tuple(str(fret) for fret in value["frets"])
        fingers_value = value.get("fingers")
        fingers = tuple(str(finger) for finger in fingers_value) if fingers_value else None
        tuning_value = value.get("tuning", ("E", "A", "D", "G", "B", "e"))
        tuning = tuple(str(string) for string in tuning_value)
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
        if isinstance(value, dict):
            raw_chords = value.get("chords", ())
            return cls(
                chords=tuple(str(chord) for chord in raw_chords),
                timestamp=value.get("timestamp"),
                provenance=parse_provenance(value.get("provenance")),
            )
        return cls(chords=tuple(str(chord) for chord in value))

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

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ChartSection:
        raw_bars = value.get("bars", [])
        bars = tuple(ChartMeasure.from_value(bar) for bar in raw_bars)
        notes = tuple(str(note) for note in value.get("notes", ()))
        return cls(
            name=str(value["name"]),
            bars=bars,
            repeat=value.get("repeat"),
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
        if not isinstance(value, dict):
            raise ValueError("Version history entries must be mappings")
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

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RecordingSource:
        if not isinstance(value, dict):
            raise ValueError("Recording source entries must be mappings")
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

    @classmethod
    def from_value(cls, value: Any) -> RecordingNote:
        if isinstance(value, dict):
            if "text" not in value:
                raise ValueError("Structured recording note entries must include text")
            return cls(
                text=str(value["text"]),
                confidence=_parse_confidence(value.get("confidence")),
                claim_origin=_parse_claim_origin(value.get("claim_origin")),
                category=_optional_str(value.get("category")),
                severity=_optional_str(value.get("severity")),
                recording_ids=_parse_recording_ids(value),
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

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RecordingNoteGroup:
        if not isinstance(value, dict):
            raise ValueError("Structured recording note groups must be mappings")
        if "name" not in value:
            raise ValueError("Structured recording note groups must include a name")
        notes = _parse_recording_notes(value.get("notes", ()))
        group_value = value.get("value")
        if group_value is not None:
            group_value = RecordingNote.from_value(group_value)
        if not notes and not group_value:
            raise ValueError("Structured recording note groups must include notes or value")
        return cls(
            name=str(value["name"]),
            notes=notes,
            value=group_value,
            category=_optional_str(value.get("category")),
            severity=_optional_str(value.get("severity")),
            recording_ids=_parse_recording_ids(value),
            provenance=parse_provenance(value.get("provenance")),
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name}
        if self.notes:
            value["notes"] = [note.to_mapping() for note in self.notes]
        if self.value:
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
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        _validate_unique_recording_ids(self.recordings)
        _validate_recording_references(self.recordings, self.structured_recording_notes)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SongChart:
        sections = tuple(ChartSection.from_mapping(section) for section in value.get("sections", ()))
        raw_history = value.get("version_history", ())
        if not isinstance(raw_history, Sequence) or isinstance(raw_history, str):
            raise ValueError("version_history must be a list of entries")
        version_history = tuple(VersionEntry.from_mapping(entry) for entry in raw_history)
        return cls(
            title=str(value["title"]),
            artist=value.get("artist"),
            key=value.get("key"),
            tuning=str(value.get("tuning", "Standard")),
            capo=str(value.get("capo", "None")),
            version=str(value.get("version", "1.0")),
            confidence=_parse_confidence(value.get("confidence")),
            sections=sections,
            voicing_notes=tuple(str(note) for note in value.get("voicing_notes", ())),
            version_history=version_history,
            analysis=dict(value.get("analysis", {})),
            performance_notes=tuple(str(note) for note in value.get("performance_notes", ())),
            recording_notes=tuple(str(note) for note in value.get("recording_notes", ())),
            recordings=_parse_recording_sources(value.get("recordings", ())),
            structured_recording_notes=_parse_recording_groups(
                value.get("structured_recording_notes", ())
            ),
            source=value.get("source"),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
            provenance=parse_provenance(value.get("provenance")),
            metadata_provenance=_parse_provenance_map(value.get("metadata_provenance")),
            chord_provenance=_parse_provenance_map(value.get("chord_provenance")),
            analysis_provenance=_parse_provenance_map(value.get("analysis_provenance")),
            performance_note_provenance=_parse_provenance_map(
                value.get("performance_note_provenance")
            ),
            recording_note_provenance=_parse_provenance_map(value.get("recording_note_provenance")),
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
            if group.value:
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


def _parse_provenance_map(value: Any) -> dict[str, tuple[ProvenanceRecord, ...]]:
    if not value:
        return {}
    return {str(key): parse_provenance(records) for key, records in value.items()}


def _parse_recording_sources(value: Any) -> tuple[RecordingSource, ...]:
    if not value:
        return ()
    if isinstance(value, dict):
        return tuple(
            RecordingSource.from_mapping(_recording_source_mapping(recording_id, source_value))
            for recording_id, source_value in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(RecordingSource.from_mapping(source) for source in value)
    raise ValueError("recordings must be a list or mapping")


def _recording_source_mapping(recording_id: Any, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"id": str(recording_id), **value}
    return {"id": str(recording_id), "title": str(value)}


def _parse_recording_groups(value: Any) -> tuple[RecordingNoteGroup, ...]:
    if not value:
        return ()
    if isinstance(value, dict):
        return tuple(
            RecordingNoteGroup.from_mapping(_recording_group_mapping(name, group_value))
            for name, group_value in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(RecordingNoteGroup.from_mapping(group) for group in value)
    raise ValueError("structured_recording_notes must be a list or mapping")


def _recording_group_mapping(name: Any, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"name": str(name), **value}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return {"name": str(name), "notes": list(value)}
    return {"name": str(name), "value": value}


def _parse_recording_notes(value: Any) -> tuple[RecordingNote, ...]:
    if not value:
        return ()
    if isinstance(value, str) or isinstance(value, dict):
        return (RecordingNote.from_value(value),)
    if isinstance(value, Sequence):
        return tuple(RecordingNote.from_value(item) for item in value)
    raise ValueError("recording note group notes must be a string, mapping, or list")


def _parse_recording_ids(value: dict[str, Any]) -> tuple[str, ...]:
    if "recording_ids" in value:
        raw_ids = value["recording_ids"]
    elif "recording_id" in value:
        raw_ids = value["recording_id"]
    else:
        return ()
    if isinstance(raw_ids, str):
        return (raw_ids,)
    if isinstance(raw_ids, Sequence):
        return tuple(str(recording_id) for recording_id in raw_ids)
    raise ValueError("recording_ids must be a string or list")


def _parse_string_tuple(value: Any, *, claim: str) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    raise ValueError(f"{claim} must be a string or list")


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


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
        if group.value:
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
    if group.value:
        recording_ids.extend(group.value.recording_ids)
    return tuple(recording_ids)


def _provenance_map_to_mapping(
    value: dict[str, tuple[ProvenanceRecord, ...]],
) -> dict[str, list[dict[str, Any]]] | None:
    if not value:
        return None
    return {key: provenance_to_mapping(records) for key, records in value.items()}
