from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from chordatlas.provenance import (
    Confidence,
    ProvenanceRecord,
    format_confidence,
    parse_provenance,
    provenance_to_mapping,
    provenance_warnings,
)


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
    source: str | None = None
    provenance: tuple[ProvenanceRecord, ...] = ()
    metadata_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    chord_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    analysis_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)
    performance_note_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(
        default_factory=dict
    )
    recording_note_provenance: dict[str, tuple[ProvenanceRecord, ...]] = field(default_factory=dict)

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
            source=value.get("source"),
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


def _parse_provenance_map(value: Any) -> dict[str, tuple[ProvenanceRecord, ...]]:
    if not value:
        return {}
    return {str(key): parse_provenance(records) for key, records in value.items()}


def _provenance_map_to_mapping(
    value: dict[str, tuple[ProvenanceRecord, ...]],
) -> dict[str, list[dict[str, Any]]] | None:
    if not value:
        return None
    return {key: provenance_to_mapping(records) for key, records in value.items()}
