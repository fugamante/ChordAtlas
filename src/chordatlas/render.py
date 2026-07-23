from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from chordatlas.chords import COMMON_CHORD_SHAPES
from chordatlas.compare import (
    category_display_labels,
    claim_label,
    comparison_to_mapping,
    counts_label,
    recording_display_labels,
)
from chordatlas.models import (
    ChartMeasure,
    ChordShape,
    RecordingNote,
    RecordingNoteGroup,
    RecordingSource,
    SongChart,
)
from chordatlas.provenance import (
    Confidence,
    ProvenanceRecord,
    VerificationStatus,
    format_confidence,
)

DIVIDER = "═" * 36
ProvenanceMode = Literal["minimal", "standard", "research"]
_UNCERTAIN_STATUSES = {
    VerificationStatus.UNVERIFIED,
    VerificationStatus.DISPUTED,
    VerificationStatus.UNKNOWN,
}


def render_chord_shape(shape: ChordShape) -> str:
    string_names = tuple(reversed(shape.tuning))
    high_to_low_frets = tuple(reversed(shape.frets))
    return "\n".join(
        f"{string}|--{fret}--" for string, fret in zip(string_names, high_to_low_frets, strict=True)
    )


def render_markdown(chart: SongChart, *, provenance_mode: ProvenanceMode = "minimal") -> str:
    lines: list[str] = []
    title = chart.title if chart.artist is None else f"{chart.title} — {chart.artist}"
    lines.append(f"# {title}")
    lines.append("")
    lines.extend(_metadata_lines(chart, markdown=True))
    lines.extend(_provenance_lines(chart.provenance, mode=provenance_mode, markdown=True))
    lines.extend(_version_history_lines(chart, markdown=True, provenance_mode=provenance_mode))
    lines.append("")
    lines.append("## Chord Reference")
    lines.append("")
    lines.extend(_chord_reference(chart, markdown=True, provenance_mode=provenance_mode))
    for section in chart.sections:
        lines.append("")
        lines.append(f"## {section.name.title()}")
        lines.extend(_provenance_lines(section.provenance, mode=provenance_mode, markdown=True))
        lines.append("")
        lines.extend(_section_lines(section.bars, markdown=True, provenance_mode=provenance_mode))
        if section.repeat:
            lines.append("")
            lines.append(f"Repeat {section.repeat}")
        if section.notes:
            lines.append("")
            lines.extend(f"- {note}" for note in section.notes)
    lines.extend(_analysis_lines(chart, markdown=True, provenance_mode=provenance_mode))
    return "\n".join(lines).rstrip() + "\n"


def render_text(chart: SongChart, *, provenance_mode: ProvenanceMode = "minimal") -> str:
    lines: list[str] = []
    title = chart.title if chart.artist is None else f"{chart.title} — {chart.artist}"
    lines.append(f"Song: {title}")
    lines.extend(_metadata_lines(chart, markdown=False))
    lines.extend(_provenance_lines(chart.provenance, mode=provenance_mode, markdown=False))
    lines.extend(_version_history_lines(chart, markdown=False, provenance_mode=provenance_mode))
    lines.append("")
    lines.append(DIVIDER)
    lines.append("CHORD REFERENCE")
    lines.append(DIVIDER)
    lines.append("")
    lines.extend(_chord_reference(chart, markdown=False, provenance_mode=provenance_mode))
    for section in chart.sections:
        lines.append("")
        lines.append(DIVIDER)
        lines.append(section.name.upper())
        lines.append(DIVIDER)
        lines.extend(_provenance_lines(section.provenance, mode=provenance_mode, markdown=False))
        lines.append("")
        lines.extend(_section_lines(section.bars, markdown=False, provenance_mode=provenance_mode))
        if section.repeat:
            lines.append("")
            lines.append(f"Repeat {section.repeat}")
        if section.notes:
            lines.append("")
            lines.extend(f"- {note}" for note in section.notes)
    lines.extend(_analysis_lines(chart, markdown=False, provenance_mode=provenance_mode))
    return "\n".join(lines).rstrip() + "\n"


def _version_history_lines(
    chart: SongChart,
    *,
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    if not chart.version_history:
        return []

    lines = ["", "## Version History" if markdown else DIVIDER]
    if not markdown:
        lines.append("VERSION HISTORY")
        lines.append(DIVIDER)
    lines.append("")
    for entry in chart.version_history:
        lines.append(f"### v{entry.version}" if markdown else f"v{entry.version}")
        lines.extend(f"- {change}" for change in entry.changes)
        lines.extend(_provenance_lines(entry.provenance, mode=provenance_mode, markdown=markdown))
        lines.append("")
    return _strip_trailing_blank(lines)


def _metadata_lines(chart: SongChart, *, markdown: bool) -> list[str]:
    fields = [
        ("Key", chart.key or "Unknown"),
        ("Tuning", chart.tuning),
        ("Capo", chart.capo),
        ("Version", chart.version),
    ]
    if chart.confidence:
        fields.append(("Confidence", format_confidence(chart.confidence)))
    if chart.source:
        fields.append(("Source", chart.source))
    if markdown:
        return [f"- **{name}:** {value}" for name, value in fields]
    return [f"{name}: {value}" for name, value in fields]


def _chord_reference(
    chart: SongChart,
    *,
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    lines: list[str] = []
    for chord in chart.used_chords:
        shape = COMMON_CHORD_SHAPES.get(chord)
        if shape is None:
            lines.append(f"{chord}")
            lines.append("No built-in guitar shape available.")
            lines.append("")
            continue
        if markdown:
            lines.append(f"### {shape.name}")
            lines.append("")
            lines.append("```text")
            lines.append(render_chord_shape(shape))
            lines.append("```")
        else:
            lines.append(shape.name)
            lines.append(render_chord_shape(shape))
        if shape.notes:
            lines.append(f"Note: {shape.notes}")
        lines.extend(
            _provenance_lines(
                chart.chord_provenance.get(chord, ()) + shape.provenance,
                mode=provenance_mode,
                markdown=markdown,
            )
        )
        lines.append("")
    return _strip_trailing_blank(lines)


def _section_lines(
    bars: Iterable[ChartMeasure],
    *,
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    lines: list[str] = []
    for measure in bars:
        prefix = f"{measure.timestamp} " if measure.timestamp else ""
        lines.append(prefix + "| " + " ".join(f"{chord:<7}" for chord in measure).rstrip() + " |")
        lines.extend(
            _provenance_lines(measure.provenance, mode=provenance_mode, markdown=markdown)
        )
    return lines


def _analysis_lines(
    chart: SongChart,
    *,
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    lines: list[str] = []
    if chart.voicing_notes:
        lines.extend(_note_block("Voicing Notes", chart.voicing_notes, markdown=markdown))
    roman = chart.analysis.get("roman")
    nashville = chart.analysis.get("nashville")
    if roman or nashville:
        lines.append("")
        lines.append("## Analysis" if markdown else DIVIDER)
        if not markdown:
            lines.append("ANALYSIS")
            lines.append(DIVIDER)
        lines.append("")
        if roman:
            lines.append("Roman numerals:")
            lines.append(_arrow_join(roman))
            lines.extend(
                _provenance_lines(
                    chart.analysis_provenance.get("roman", ()),
                    mode=provenance_mode,
                    markdown=markdown,
                )
            )
            lines.append("")
        if nashville:
            lines.append("Nashville:")
            lines.append(_arrow_join(nashville))
            lines.extend(
                _provenance_lines(
                    chart.analysis_provenance.get("nashville", ()),
                    mode=provenance_mode,
                    markdown=markdown,
                )
            )
            lines.append("")
    if chart.performance_notes:
        lines.extend(_note_block("Performance Notes", chart.performance_notes, markdown=markdown))
    if chart.recordings or chart.recording_notes or chart.structured_recording_notes:
        lines.extend(
            _recording_notes_lines(
                chart,
                markdown=markdown,
                provenance_mode=provenance_mode,
            )
        )
        lines.extend(_recording_comparison_lines(chart, markdown=markdown))
    return _strip_trailing_blank(lines)


def _recording_notes_lines(
    chart: SongChart,
    *,
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    if chart.recording_notes and not chart.structured_recording_notes:
        return _note_block("Recording Notes", chart.recording_notes, markdown=markdown)

    lines = ["", "## Recording Notes" if markdown else DIVIDER]
    if not markdown:
        lines.append("RECORDING NOTES")
        lines.append(DIVIDER)
    lines.append("")

    if chart.recordings:
        lines.extend(_recording_sources_lines(chart.recordings, markdown=markdown))
        lines.append("")

    if chart.recording_notes:
        lines.extend(_recording_group_lines(
            RecordingNoteGroup(
                name="General",
                notes=tuple(RecordingNote(text=note) for note in chart.recording_notes),
            ),
            recordings=chart.recordings,
            markdown=markdown,
            provenance_mode=provenance_mode,
        ))
        lines.append("")

    for group in chart.structured_recording_notes:
        lines.extend(
            _recording_group_lines(
                group,
                recordings=chart.recordings,
                markdown=markdown,
                provenance_mode=provenance_mode,
            )
        )
        lines.append("")

    return _strip_trailing_blank(lines)


def _recording_sources_lines(
    recordings: tuple[RecordingSource, ...],
    *,
    markdown: bool,
) -> list[str]:
    lines = ["### Recordings" if markdown else "Recordings"]
    for recording in recordings:
        details = [recording.version_label, recording.source_url]
        detail = " | ".join(item for item in details if item)
        label = f"{recording.id}: {recording.title}"
        if detail:
            label = f"{label} ({detail})"
        lines.append(f"- {label}" if markdown else f"- {label}")
        for note in recording.notes:
            lines.append(f"  - {note}")
    return lines


def _recording_comparison_lines(chart: SongChart, *, markdown: bool) -> list[str]:
    data = comparison_to_mapping(chart)
    if not data["categories"]:
        return []

    lines = ["", "## Recording Source Comparison" if markdown else DIVIDER]
    if not markdown:
        lines.append("RECORDING SOURCE COMPARISON")
        lines.append(DIVIDER)
    lines.append("")
    recording_labels = recording_display_labels(
        (recording["id"], recording["title"]) for recording in data["recordings"]
    )
    category_labels = category_display_labels(
        (category["category"], category["label"]) for category in data["categories"]
    )
    lines.extend(_recording_summary_lines(data, markdown=markdown))
    lines.append("")

    for category in data["categories"]:
        label = category_labels[category["category"]]
        lines.append(f"### {label}" if markdown else label)
        for recording in category["recordings"]:
            label = recording_labels[recording["id"]]
            lines.append(f"#### {label}" if markdown else label)
            claims = recording["claims"]
            if claims:
                lines.extend(f"- {claim_label(claim)}" for claim in claims)
            else:
                lines.append("- No scoped recording-note claims.")
            lines.append("")
        lines.extend(
            _recording_difference_lines(
                category,
                markdown=markdown,
                recording_labels=recording_labels,
            )
        )
        lines.append("")
    return _strip_trailing_blank(lines)


def _recording_summary_lines(data: dict[str, Any], *, markdown: bool) -> list[str]:
    summary = data["summary"]
    lines = ["### Summary" if markdown else "Summary"]
    category_labels = category_display_labels(
        (category["category"], category["label"])
        for category in summary["categories"]
    )
    for category in summary["categories"]:
        line = (
            f"{category_labels[category['category']]}: "
            f"{category['shared_count']} shared, "
            f"{category['source_specific_count']} source-specific"
        )
        if category["severity_counts"]:
            line = f"{line}; severity {counts_label(category['severity_counts'])}"
        lines.append(f"- {line}")

    lines.append("")
    lines.append("By source:")
    recording_labels = recording_display_labels(
        (recording["id"], recording["title"])
        for recording in summary["recordings"]
    )
    for recording in summary["recordings"]:
        lines.append(
            f"- {recording_labels[recording['id']]}: {recording['claim_count']} claims, "
            f"{recording['source_specific_count']} source-specific"
        )
    return lines


def _recording_difference_lines(
    category: dict[str, Any],
    *,
    markdown: bool,
    recording_labels: dict[str, str],
) -> list[str]:
    lines = ["#### Differences" if markdown else "Differences"]
    if not category["differences"]:
        lines.append("- No source-specific differences in scoped recording notes.")
        return lines

    for difference in category["differences"]:
        lines.append(f"- {recording_labels[difference['recording_id']]}:")
        lines.extend(f"  - {claim_label(claim)}" for claim in difference["claims"])
    return lines


def _recording_group_lines(
    group: RecordingNoteGroup,
    *,
    recordings: tuple[RecordingSource, ...],
    markdown: bool,
    provenance_mode: ProvenanceMode,
) -> list[str]:
    scope = _recording_scope(group.recording_ids, recordings)
    title = group.name + (f" [{scope}]" if scope else "")
    lines = [f"### {title}" if markdown else title]
    if group.value is not None:
        lines.extend(
            _recording_note_lines(
                group.value,
                recordings=recordings,
                markdown=markdown,
                provenance_mode=provenance_mode,
                marker="",
            )
        )
    if group.notes:
        marker = "- " if markdown else "✓ "
        for note in group.notes:
            lines.extend(
                _recording_note_lines(
                    note,
                    recordings=recordings,
                    markdown=markdown,
                    provenance_mode=provenance_mode,
                    marker=marker,
                )
            )
    lines.extend(_provenance_lines(group.provenance, mode=provenance_mode, markdown=markdown))
    return lines


def _recording_note_lines(
    note: RecordingNote,
    *,
    recordings: tuple[RecordingSource, ...],
    markdown: bool,
    provenance_mode: ProvenanceMode,
    marker: str,
) -> list[str]:
    scope = _recording_scope(note.recording_ids, recordings)
    scope_label = f" [{scope}]" if scope else ""
    lines = [f"{marker}{note.text}{scope_label}{_recording_note_suffix(note, provenance_mode)}"]
    if provenance_mode in {"standard", "research"}:
        lines.extend(_provenance_lines(note.provenance, mode=provenance_mode, markdown=markdown))
    return lines


def _recording_scope(
    recording_ids: tuple[str, ...],
    recordings: tuple[RecordingSource, ...],
) -> str:
    if not recording_ids:
        return ""
    labels_by_id = recording_display_labels(
        (recording.id, recording.title) for recording in recordings
    )
    return ", ".join(labels_by_id.get(recording_id, recording_id) for recording_id in recording_ids)


def _recording_note_suffix(note: RecordingNote, provenance_mode: ProvenanceMode) -> str:
    if provenance_mode == "minimal":
        return ""
    parts: list[str] = []
    if note.claim_origin:
        parts.append(note.claim_origin.value.replace("_", " "))
    if note.confidence:
        parts.append(f"{format_confidence(note.confidence)} confidence")
    if note.severity:
        parts.append(f"{note.severity} severity")
    return f" ({', '.join(parts)})" if parts else ""


def _note_block(title: str, notes: Iterable[str], *, markdown: bool) -> list[str]:
    lines = ["", f"## {title}" if markdown else title + ":", ""]
    lines.extend(f"- {note}" for note in notes)
    return lines


def _arrow_join(values: Iterable[object]) -> str:
    return " -> ".join(str(value) for value in values)


def _provenance_lines(
    records: tuple[ProvenanceRecord, ...],
    *,
    mode: ProvenanceMode,
    markdown: bool,
) -> list[str]:
    visible = tuple(record for record in records if _should_show(record, mode))
    if not visible:
        return []

    lines: list[str] = []
    for record in visible:
        confidence = format_confidence(record.confidence)
        status = record.verification_status.value.title()
        if mode == "minimal":
            summary = _evidence_summary(record)
            provenance = f"Provenance: {status}" + (f", {summary}" if summary else "")
            lines.append(_prefix(markdown) + provenance)
        elif mode == "standard":
            if confidence:
                lines.append(_prefix(markdown) + f"Confidence: {confidence}")
            lines.append(_prefix(markdown) + f"Status: {status}")
            summary = _evidence_summary(record)
            if summary:
                lines.append(_prefix(markdown) + f"Evidence: {summary}")
        else:
            lines.append(_prefix(markdown) + f"Source type: {record.source_type.value}")
            if record.source_name:
                lines.append(_prefix(markdown) + f"Source: {record.source_name}")
            if record.source_url:
                lines.append(_prefix(markdown) + f"URL: {record.source_url}")
            if record.timestamp_range:
                lines.append(_prefix(markdown) + f"Timestamp: {record.timestamp_range}")
            if record.method:
                lines.append(_prefix(markdown) + f"Method: {record.method}")
            if record.contributor:
                lines.append(_prefix(markdown) + f"Contributor: {record.contributor}")
            if confidence:
                lines.append(_prefix(markdown) + f"Confidence: {confidence}")
            lines.append(_prefix(markdown) + f"Status: {status}")
            if record.notes:
                lines.append(_prefix(markdown) + f"Notes: {record.notes}")
            for ref in record.evidence_refs:
                detail = ref.timestamp_range or ref.source_name or ref.source_url or ref.ref_id
                lines.append(_prefix(markdown) + f"Evidence {ref.ref_id}: {detail}")
    return lines


def _should_show(record: ProvenanceRecord, mode: ProvenanceMode) -> bool:
    if mode == "research":
        return True
    if mode == "standard":
        return True
    return (
        record.confidence in {Confidence.LOW, Confidence.MEDIUM}
        or record.verification_status in _UNCERTAIN_STATUSES
        or record.notes is not None
    )


def _evidence_summary(record: ProvenanceRecord) -> str:
    parts = [
        record.timestamp_range,
        record.method,
        ", ".join(ref.timestamp_range or ref.ref_id for ref in record.evidence_refs),
    ]
    return ", ".join(part for part in parts if part)


def _prefix(markdown: bool) -> str:
    return "- " if markdown else ""


def _strip_trailing_blank(lines: list[str]) -> list[str]:
    while lines and lines[-1] == "":
        lines.pop()
    return lines
