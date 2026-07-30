from __future__ import annotations

import csv
import io
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal

from chordatlas.models import RecordingNote, RecordingNoteGroup, RecordingSource, SongChart
from chordatlas.provenance import ProvenanceRecord, provenance_to_mapping

COMPARISON_SCHEMA_VERSION = "1.0.0"
COMPARISON_METADATA_SCHEMA_VERSION = "1.0.0"
ProvenanceMode = Literal["minimal", "standard", "research"]
ClaimKey = tuple[str, str]
CSV_COLUMNS = (
    "category",
    "category_label",
    "recording_id",
    "recording_title",
    "group",
    "text",
    "severity",
    "source_specific",
)
EXTENDED_CSV_COLUMNS = CSV_COLUMNS + (
    "confidence",
    "claim_origin",
    "provenance_summary",
    "evidence_refs",
    "recording_source_url",
    "recording_version_label",
)


@dataclass(frozen=True)
class ComparisonFilters:
    categories: tuple[str, ...] = ()
    recording_ids: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    source_specific_only: bool = False

    @classmethod
    def from_values(
        cls,
        *,
        categories: tuple[str, ...] | list[str] | None = None,
        recording_ids: tuple[str, ...] | list[str] | None = None,
        severities: tuple[str, ...] | list[str] | None = None,
        source_specific_only: bool = False,
    ) -> ComparisonFilters:
        return cls(
            categories=tuple(categories or ()),
            recording_ids=tuple(recording_ids or ()),
            severities=tuple(severities or ()),
            source_specific_only=source_specific_only,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "categories": list(self.categories),
            "recording_ids": list(self.recording_ids),
            "severities": list(self.severities),
            "source_specific_only": self.source_specific_only,
        }


@dataclass(frozen=True)
class SourceClaim:
    group: str
    text: str
    severity: str | None = None
    confidence: str | None = None
    claim_origin: str | None = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def key(self) -> ClaimKey:
        return (self.group, self.text)

    def semantic_key(self) -> tuple[str, str, str | None, str | None, str | None]:
        return (
            self.group,
            self.text,
            self.severity,
            self.confidence,
            self.claim_origin,
        )

    def to_mapping(
        self,
        *,
        source_specific: bool,
        provenance_mode: ProvenanceMode,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "group": self.group,
            "text": self.text,
            "source_specific": source_specific,
        }
        if self.severity:
            value["severity"] = self.severity
        if provenance_mode != "minimal":
            if self.confidence:
                value["confidence"] = self.confidence
            if self.claim_origin:
                value["claim_origin"] = self.claim_origin
            provenance_summary = _provenance_summary(self.provenance)
            if provenance_summary:
                value["provenance_summary"] = provenance_summary
        if provenance_mode == "research":
            evidence_refs = _evidence_refs(self.provenance)
            if evidence_refs:
                value["evidence_refs"] = evidence_refs
            if self.provenance:
                value["provenance"] = provenance_to_mapping(self.provenance)
        return value


def comparison_to_mapping(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "minimal",
) -> dict[str, Any]:
    """Return a machine-readable recording comparison analytics export."""

    active_filters = filters or ComparisonFilters()
    recordings = _filtered_recordings(chart.recordings, active_filters)
    scope_filters = ComparisonFilters.from_values(
        categories=active_filters.categories,
        severities=active_filters.severities,
    )
    source_scope_claims = _claims_by_category(chart, chart.recordings, scope_filters)
    claims_by_category = _claims_by_category(chart, recordings, active_filters)
    categories = [
        _category_mapping(
            category,
            claims_by_id,
            recordings,
            active_filters,
            shared_keys=_shared_keys(source_scope_claims.get(category, {})),
            provenance_mode=provenance_mode,
        )
        for category, claims_by_id in claims_by_category.items()
    ]
    categories = [category for category in categories if category["claim_count"] > 0]
    summary = _summary_mapping(categories, recordings)
    return {
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "chart_title": chart.title,
        "chart_artist": chart.artist,
        "recordings": [recording.to_mapping() for recording in recordings],
        "summary": summary,
        "categories": categories,
    }


def comparison_to_json(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "minimal",
    indent: int = 2,
) -> str:
    data = comparison_to_mapping(chart, filters=filters, provenance_mode=provenance_mode)
    return json.dumps(data, indent=indent, sort_keys=True, allow_nan=False) + "\n"


def comparison_to_csv(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "minimal",
) -> str:
    data = comparison_to_mapping(chart, filters=filters, provenance_mode=provenance_mode)
    output = io.StringIO()
    columns = CSV_COLUMNS if provenance_mode == "minimal" else EXTENDED_CSV_COLUMNS
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for category in data["categories"]:
        for recording in category["recordings"]:
            for claim in recording["claims"]:
                row = _csv_row(category, recording, claim)
                if provenance_mode != "minimal":
                    row.update(_extended_csv_row(recording, claim))
                writer.writerow(row)
    return output.getvalue()


def comparison_metadata_to_mapping(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "research",
) -> dict[str, Any]:
    active_filters = filters or ComparisonFilters()
    comparison = comparison_to_mapping(
        chart,
        filters=active_filters,
        provenance_mode=provenance_mode,
    )
    provenance_records = _provenance_records(comparison)
    return {
        "metadata_schema_version": COMPARISON_METADATA_SCHEMA_VERSION,
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "chart_schema_version": chart.schema_version,
        "chart_title": chart.title,
        "chart_artist": chart.artist,
        "export": {
            "format": "csv",
            "provenance_mode": provenance_mode,
            "filters": active_filters.to_mapping(),
            "csv_columns": list(
                CSV_COLUMNS if provenance_mode == "minimal" else EXTENDED_CSV_COLUMNS
            ),
        },
        "recordings": comparison["recordings"],
        "summary": comparison["summary"],
        "provenance_records": provenance_records,
        "evidence_refs": _metadata_evidence_refs(provenance_records),
        "comparison": comparison,
    }


def comparison_metadata_to_json(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "research",
    indent: int = 2,
) -> str:
    data = comparison_metadata_to_mapping(
        chart,
        filters=filters,
        provenance_mode=provenance_mode,
    )
    return json.dumps(data, indent=indent, sort_keys=True, allow_nan=False) + "\n"


def render_comparison_markdown(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "minimal",
) -> str:
    data = comparison_to_mapping(chart, filters=filters, provenance_mode=provenance_mode)
    title = data["chart_title"]
    if data["chart_artist"]:
        title = f"{title} — {data['chart_artist']}"
    recording_labels = recording_display_labels(
        (recording["id"], recording["title"]) for recording in data["recordings"]
    )
    category_labels = category_display_labels(
        (category["category"], category["label"]) for category in data["categories"]
    )
    lines = [f"# Recording Comparison: {title}", ""]
    lines.extend(summary_lines(data, markdown=True))
    for category in data["categories"]:
        lines.append("")
        lines.append(f"## {category_labels[category['category']]}")
        for recording in category["recordings"]:
            lines.append("")
            lines.append(f"### {recording_labels[recording['id']]}")
            if recording["claims"]:
                lines.extend(f"- {claim_label(claim)}" for claim in recording["claims"])
            else:
                lines.append("- No scoped recording-note claims.")
        lines.append("")
        lines.append("### Differences")
        if category["differences"]:
            for difference in category["differences"]:
                lines.append(f"- {recording_labels[difference['recording_id']]}:")
                lines.extend(f"  - {claim_label(claim)}" for claim in difference["claims"])
        else:
            lines.append("- No source-specific differences in scoped recording notes.")
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_text(
    chart: SongChart,
    *,
    filters: ComparisonFilters | None = None,
    provenance_mode: ProvenanceMode = "minimal",
) -> str:
    data = comparison_to_mapping(chart, filters=filters, provenance_mode=provenance_mode)
    title = data["chart_title"]
    if data["chart_artist"]:
        title = f"{title} — {data['chart_artist']}"
    recording_labels = recording_display_labels(
        (recording["id"], recording["title"]) for recording in data["recordings"]
    )
    category_labels = category_display_labels(
        (category["category"], category["label"]) for category in data["categories"]
    )
    lines = [f"Recording Comparison: {title}", ""]
    lines.extend(summary_lines(data, markdown=False))
    for category in data["categories"]:
        lines.append("")
        display_label = category_labels[category["category"]]
        if display_label == category["label"]:
            lines.append(display_label.upper())
        else:
            lines.append(f"{category['label'].upper()} [{category['category']}]")
        for recording in category["recordings"]:
            lines.append("")
            lines.append(recording_labels[recording["id"]])
            if recording["claims"]:
                lines.extend(f"- {claim_label(claim)}" for claim in recording["claims"])
            else:
                lines.append("- No scoped recording-note claims.")
        lines.append("")
        lines.append("Differences")
        if category["differences"]:
            for difference in category["differences"]:
                lines.append(f"- {recording_labels[difference['recording_id']]}:")
                lines.extend(f"  - {claim_label(claim)}" for claim in difference["claims"])
        else:
            lines.append("- No source-specific differences in scoped recording notes.")
    return "\n".join(lines).rstrip() + "\n"


def summary_lines(data: dict[str, Any], *, markdown: bool) -> list[str]:
    heading = "## Summary" if markdown else "Summary"
    lines = [heading]
    summary = data["summary"]
    lines.extend(
        [
            f"- Recordings: {summary['recording_count']}",
            f"- Categories: {summary['category_count']}",
            f"- Claim assignments: {summary['claim_count']}",
            f"- Shared claims: {summary['shared_claim_count']}",
            f"- Source-specific claims: {summary['source_specific_claim_count']}",
        ]
    )
    if summary["severity_counts"]:
        lines.append(f"- Severity: {counts_label(summary['severity_counts'])}")
    lines.append("")
    lines.append("By category:")
    category_labels = category_display_labels(
        (category["category"], category["label"])
        for category in summary["categories"]
    )
    for category in summary["categories"]:
        line = (
            f"- {category_labels[category['category']]}: {category['claim_count']} claims, "
            f"{category['shared_count']} shared, "
            f"{category['source_specific_count']} source-specific"
        )
        if category["severity_counts"]:
            line = f"{line}; severity {counts_label(category['severity_counts'])}"
        lines.append(line)
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


def recording_display_labels(recordings: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return human labels that qualify only colliding titles with stable IDs."""

    rows = tuple(recordings)
    title_counts = Counter(title for _, title in rows)
    return {
        recording_id: f"{title} [{recording_id}]" if title_counts[title] > 1 else title
        for recording_id, title in rows
    }


def category_display_labels(categories: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return human labels that qualify only colliding labels with raw category keys."""

    rows = tuple(categories)
    label_counts = Counter(label for _, label in rows)
    return {
        category: f"{label} [{category}]" if label_counts[label] > 1 else label
        for category, label in rows
    }


def claim_label(claim: dict[str, Any]) -> str:
    label = f"{claim['group']}: {claim['text']}"
    if claim.get("severity"):
        label = f"{label} [{claim['severity']} severity]"
    details = []
    if claim.get("claim_origin"):
        details.append(str(claim["claim_origin"]))
    if claim.get("confidence"):
        details.append(f"{claim['confidence']} confidence")
    if claim.get("provenance_summary"):
        details.append(str(claim["provenance_summary"]))
    if details:
        label = f"{label} ({'; '.join(details)})"
    return label


def counts_label(counts: dict[str, int]) -> str:
    return ", ".join(f"{label}={count}" for label, count in counts.items())


def _csv_row(
    category: dict[str, Any],
    recording: dict[str, Any],
    claim: dict[str, Any],
) -> dict[str, str]:
    return {
        "category": category["category"],
        "category_label": category["label"],
        "recording_id": recording["id"],
        "recording_title": recording["title"],
        "group": claim["group"],
        "text": claim["text"],
        "severity": claim.get("severity", ""),
        "source_specific": str(claim["source_specific"]).lower(),
    }


def _extended_csv_row(recording: dict[str, Any], claim: dict[str, Any]) -> dict[str, str]:
    return {
        "confidence": claim.get("confidence", ""),
        "claim_origin": claim.get("claim_origin", ""),
        "provenance_summary": claim.get("provenance_summary", ""),
        "evidence_refs": _csv_evidence_refs(claim.get("evidence_refs", ())),
        "recording_source_url": recording.get("source_url", ""),
        "recording_version_label": recording.get("version_label", ""),
    }


def _recording_provenance_context(
    recording: RecordingSource,
    provenance_mode: ProvenanceMode,
) -> dict[str, Any]:
    if provenance_mode == "minimal":
        return {}
    context: dict[str, Any] = {}
    if recording.source_url:
        context["source_url"] = recording.source_url
    if recording.version_label:
        context["version_label"] = recording.version_label
    if provenance_mode == "research" and recording.provenance:
        context["provenance"] = provenance_to_mapping(recording.provenance)
    return context


def _provenance_summary(records: tuple[ProvenanceRecord, ...]) -> str | None:
    if not records:
        return None
    labels = []
    for record in records:
        parts = [record.source_type.value]
        if record.source_name:
            parts.append(record.source_name)
        if record.timestamp_range:
            parts.append(record.timestamp_range)
        if record.method:
            parts.append(record.method)
        if record.verification_status.value != "unknown":
            parts.append(record.verification_status.value)
        labels.append(" / ".join(parts))
    return "; ".join(labels)


def _evidence_refs(records: tuple[ProvenanceRecord, ...]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for record in records:
        refs.extend(ref.to_mapping() for ref in record.evidence_refs)
    return refs


def _csv_evidence_refs(refs: Any) -> str:
    values = []
    for ref in refs or ():
        if isinstance(ref, dict):
            values.append(str(ref.get("ref_id", "")))
        else:
            values.append(str(ref))
    return ";".join(value for value in values if value)


def _provenance_records(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for recording in comparison["recordings"]:
        for record in recording.get("provenance", ()):
            _append_unique_mapping(records, seen, record)
    for category in comparison["categories"]:
        for recording in category["recordings"]:
            for record in recording.get("provenance", ()):
                _append_unique_mapping(records, seen, record)
            for claim in recording["claims"]:
                for record in claim.get("provenance", ()):
                    _append_unique_mapping(records, seen, record)
    return records


def _metadata_evidence_refs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        for ref in record.get("evidence_refs", ()):
            _append_unique_mapping(refs, seen, ref)
    return refs


def _append_unique_mapping(
    values: list[dict[str, Any]],
    seen: set[str],
    value: dict[str, Any],
) -> None:
    key = json.dumps(value, sort_keys=True, allow_nan=False)
    if key not in seen:
        values.append(value)
        seen.add(key)


def _filtered_recordings(
    recordings: tuple[RecordingSource, ...],
    filters: ComparisonFilters,
) -> tuple[RecordingSource, ...]:
    if not filters.recording_ids:
        return recordings
    known_ids = {recording.id for recording in recordings}
    unknown_ids = tuple(
        recording_id for recording_id in filters.recording_ids if recording_id not in known_ids
    )
    if unknown_ids:
        known = ", ".join(sorted(known_ids)) or "<none>"
        unknown = ", ".join(unknown_ids)
        raise ValueError(f"Unknown recording filter(s): {unknown}. Known recordings: {known}")
    requested = set(filters.recording_ids)
    return tuple(recording for recording in recordings if recording.id in requested)


def _category_mapping(
    category: str,
    claims_by_id: dict[str, tuple[SourceClaim, ...]],
    recordings: tuple[RecordingSource, ...],
    filters: ComparisonFilters,
    *,
    shared_keys: set[ClaimKey],
    provenance_mode: ProvenanceMode,
) -> dict[str, Any]:
    recording_rows = []
    differences = []
    for recording in recordings:
        claims = claims_by_id[recording.id]
        claim_rows = [
            claim.to_mapping(
                source_specific=claim.key() not in shared_keys,
                provenance_mode=provenance_mode,
            )
            for claim in claims
            if _include_claim(claim, claim.key() not in shared_keys, filters)
        ]
        source_specific = [claim for claim in claim_rows if claim["source_specific"]]
        recording_rows.append(
            {
                "id": recording.id,
                "title": recording.title,
                "claim_count": len(claim_rows),
                "source_specific_count": len(source_specific),
                "claims": claim_rows,
                **_recording_provenance_context(recording, provenance_mode),
            }
        )
        if source_specific:
            differences.append(
                {
                    "recording_id": recording.id,
                    "title": recording.title,
                    "claims": source_specific,
                    **_recording_provenance_context(recording, provenance_mode),
                }
            )
    return {
        "category": category,
        "label": _label(category),
        "claim_count": sum(row["claim_count"] for row in recording_rows),
        "shared_count": _visible_shared_count(recording_rows),
        "source_specific_count": sum(row["source_specific_count"] for row in recording_rows),
        "severity_counts": _severity_counts(recording_rows),
        "recordings": recording_rows,
        "differences": differences,
    }


def _summary_mapping(
    categories: list[dict[str, Any]],
    recordings: tuple[RecordingSource, ...],
) -> dict[str, Any]:
    recording_rows = []
    for recording in recordings:
        claim_count = sum(
            row["claim_count"]
            for category in categories
            for row in category["recordings"]
            if row["id"] == recording.id
        )
        source_specific_count = sum(
            row["source_specific_count"]
            for category in categories
            for row in category["recordings"]
            if row["id"] == recording.id
        )
        recording_rows.append(
            {
                "id": recording.id,
                "title": recording.title,
                "claim_count": claim_count,
                "source_specific_count": source_specific_count,
            }
        )
    severity_counts: Counter[str] = Counter()
    for category in categories:
        severity_counts.update(category["severity_counts"])
    return {
        "recording_count": len(recordings),
        "category_count": len(categories),
        "claim_count": sum(category["claim_count"] for category in categories),
        "shared_claim_count": sum(category["shared_count"] for category in categories),
        "source_specific_claim_count": sum(
            category["source_specific_count"] for category in categories
        ),
        "severity_counts": _ordered_counts(dict(severity_counts)),
        "categories": [
            {
                "category": category["category"],
                "label": category["label"],
                "claim_count": category["claim_count"],
                "shared_count": category["shared_count"],
                "source_specific_count": category["source_specific_count"],
                "severity_counts": category["severity_counts"],
            }
            for category in categories
        ],
        "recordings": recording_rows,
    }


def _claims_by_category(
    chart: SongChart,
    recordings: tuple[RecordingSource, ...],
    filters: ComparisonFilters,
) -> dict[str, dict[str, tuple[SourceClaim, ...]]]:
    if not recordings:
        return {}
    recording_ids = tuple(recording.id for recording in recordings)
    claims_by_category: dict[str, dict[str, list[SourceClaim]]] = {}
    for group in chart.structured_recording_notes:
        for note in group.notes:
            _append_claim(claims_by_category, group, note, recording_ids, filters)
        if group.value is not None:
            _append_claim(claims_by_category, group, group.value, recording_ids, filters)
    return {
        category: {recording_id: tuple(claims) for recording_id, claims in claims_by_id.items()}
        for category, claims_by_id in claims_by_category.items()
        if any(claims_by_id.values())
    }


def _append_claim(
    claims_by_category: dict[str, dict[str, list[SourceClaim]]],
    group: RecordingNoteGroup,
    note: RecordingNote,
    recording_ids: tuple[str, ...],
    filters: ComparisonFilters,
) -> None:
    category = note.category or group.category or group.name
    if filters.categories and category not in set(filters.categories):
        return
    if filters.severities and not _severity_matches(note.severity or group.severity, filters):
        return
    claims_by_id = claims_by_category.setdefault(
        category,
        {recording_id: [] for recording_id in recording_ids},
    )
    claim = SourceClaim(
        group=group.name,
        text=note.text,
        severity=note.severity or group.severity,
        confidence=note.confidence.value if note.confidence else None,
        claim_origin=note.claim_origin.value if note.claim_origin else None,
        provenance=group.provenance + note.provenance,
    )
    effective_ids = note.recording_ids or group.recording_ids or recording_ids
    for recording_id in effective_ids:
        if recording_id in claims_by_id:
            _append_unique(claims_by_id[recording_id], claim)


def _include_claim(claim: SourceClaim, source_specific: bool, filters: ComparisonFilters) -> bool:
    if filters.source_specific_only and not source_specific:
        return False
    return _severity_matches(claim.severity, filters)


def _severity_matches(severity: str | None, filters: ComparisonFilters) -> bool:
    if not filters.severities:
        return True
    if severity is None:
        return False
    allowed = {value.lower() for value in filters.severities}
    return severity.lower() in allowed


def _append_unique(claims: list[SourceClaim], claim: SourceClaim) -> None:
    unique_provenance = tuple(dict.fromkeys(claim.provenance))
    if unique_provenance != claim.provenance:
        claim = replace(claim, provenance=unique_provenance)
    for index, existing in enumerate(claims):
        if existing.key() != claim.key():
            continue
        if existing.semantic_key() == claim.semantic_key():
            merged = tuple(dict.fromkeys(existing.provenance + claim.provenance))
            if merged != existing.provenance:
                claims[index] = replace(existing, provenance=merged)
        return
    claims.append(claim)


def _shared_keys(claims_by_id: dict[str, tuple[SourceClaim, ...]]) -> set[ClaimKey]:
    claim_sets = [set(claim.key() for claim in claims) for claims in claims_by_id.values()]
    return set.intersection(*claim_sets) if claim_sets else set()


def _visible_shared_count(recording_rows: list[dict[str, Any]]) -> int:
    claim_sets = [
        set((claim["group"], claim["text"]) for claim in row["claims"])
        for row in recording_rows
    ]
    if not claim_sets:
        return 0
    return len(set.intersection(*claim_sets))


def _severity_counts(recording_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in recording_rows:
        for claim in row["claims"]:
            if claim.get("severity"):
                counts[claim["severity"]] += 1
    return _ordered_counts(dict(counts))


def _ordered_counts(counts: dict[str, int]) -> dict[str, int]:
    order = {"high": 0, "medium": 1, "low": 2}
    return {
        label: counts[label]
        for label in sorted(counts, key=lambda label: (order.get(label.lower(), 99), label.lower()))
    }


def _label(value: str) -> str:
    return value.replace("_", " ").title()
