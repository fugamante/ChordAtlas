from __future__ import annotations

import re
from dataclasses import dataclass

from chordatlas.analysis.models import ChordCandidateTimeline
from chordatlas.chords import get_chord_shape
from chordatlas.models import ChartMeasure, ChartSection, SongChart
from chordatlas.promotion.models import (
    GuitarDecision,
    MappingConfig,
    MeasureProjection,
    PromotionError,
    PromotionIssue,
)
from chordatlas.review.models import ReviewedTimeline


@dataclass(frozen=True)
class PromotionDraft:
    chart: SongChart
    issues: tuple[PromotionIssue, ...]
    timing_map: tuple[MeasureProjection, ...]


def build_draft(
    reviewed: ReviewedTimeline,
    candidates: ChordCandidateTimeline,
    config: MappingConfig,
) -> PromotionDraft:
    if reviewed.phase != "ready_for_approval":
        raise PromotionError(
            "review_not_ready",
            "Only a ready_for_approval review revision can be promoted.",
        )
    if (
        reviewed.base_timeline_id != candidates.id
        or reviewed.timebase != candidates.timebase
    ):
        raise PromotionError(
            "promotion_integrity",
            "The review and candidate timeline do not match.",
        )
    boundaries = config.measure_boundaries_frames
    if (
        boundaries[0] != reviewed.analyzed_range.start_frame
        or boundaries[-1] != reviewed.analyzed_range.end_frame
    ):
        raise PromotionError(
            "invalid_grid",
            "Measure boundaries must cover the exact analyzed range.",
        )
    markers = {item.frame: item.label for item in reviewed.section_markers}
    if any(frame not in boundaries[:-1] for frame in markers):
        raise PromotionError(
            "section_off_grid",
            "Every section marker must equal an approved measure boundary.",
        )

    issues: list[PromotionIssue] = []
    beat_set = set(candidates.beats)
    for frame in boundaries[1:-1]:
        if frame not in beat_set:
            issues.append(
                PromotionIssue.create(
                    "material",
                    "timing.measure_boundary_not_detected_beat",
                    "A confirmed measure boundary is not a detected beat hypothesis.",
                    {"frame": frame},
                )
            )
    for segment in reviewed.segments[1:]:
        frame = segment.frame_range.start_frame
        if frame not in beat_set:
            issues.append(
                PromotionIssue.create(
                    "material",
                    "timing.chord_change_off_detected_beat",
                    "An exact reviewed chord change is not a detected beat hypothesis.",
                    {"frame": frame},
                )
            )
    for raw in sorted(
        {
            item.label
            for item in reviewed.segments
            if item.state == "chord"
            and item.label is not None
            and _export_label(item.state, item.label) != item.label
        }
    ):
        issues.append(
            PromotionIssue.create(
                "material",
                "label.safe_notation_normalization",
                "A reviewed detector-style chord label is normalized for SongChart.",
                {"reviewed": raw, "exported": _export_label("chord", raw)},
            )
        )

    normalized_labels = {
        _export_label(item.state, item.label) for item in reviewed.segments
    }
    decisions = {item.chord: item for item in config.guitar_decisions}
    expected_decisions = normalized_labels - {"N.C."}
    if set(decisions) != expected_decisions:
        missing = sorted(expected_decisions - set(decisions))
        extra = sorted(set(decisions) - expected_decisions)
        raise PromotionError(
            "guitar_review_incomplete",
            f"Guitar decisions do not match reviewed chords (missing={missing}, extra={extra}).",
        )
    if config.tuning != "Standard":
        raise PromotionError(
            "unsupported_tuning",
            "Stage 4 cannot safely render built-in diagrams for non-standard tuning.",
        )
    _guitar_issues(decisions, issues)

    measure_rows: list[tuple[int, str, ChartMeasure, MeasureProjection]] = []
    current_section = config.default_section_name
    section_ordinal = 0
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        detected_beats = sum(start <= frame < end for frame in candidates.beats)
        if detected_beats != 4:
            issues.append(
                PromotionIssue.create(
                    "material",
                    "timing.detected_beat_count_differs_from_four",
                    (
                        "The detector proposed a beat count other than four in this "
                        "measure. Detector beats have no reviewed beat unit and do "
                        "not override the confirmed grid."
                    ),
                    {
                        "measure": index,
                        "start_frame": start,
                        "end_frame": end,
                        "detected_beats": detected_beats,
                    },
                )
            )
        if start in markers:
            current_section = markers[start]
            section_ordinal += 1
        spans: list[tuple[str, int, int]] = []
        for segment in reviewed.segments:
            overlap_start = max(start, segment.frame_range.start_frame)
            overlap_end = min(end, segment.frame_range.end_frame)
            if overlap_start < overlap_end:
                spans.append(
                    (
                        _export_label(segment.state, segment.label),
                        overlap_start,
                        overlap_end,
                    )
                )
        if not spans:
            raise PromotionError(
                "promotion_integrity",
                "An approved measure has no reviewed chord coverage.",
            )
        if len(spans) > 1:
            issues.append(
                PromotionIssue.create(
                    "material",
                    "timing.chord_duration_not_representable",
                    "SongChart preserves chord order here but not exact position or duration.",
                    {
                        "measure": index,
                        "start_frame": start,
                        "end_frame": end,
                        "chord_count": len(spans),
                    },
                )
            )
        timestamp = _timestamp(start, reviewed.timebase.sample_rate)
        measure_rows.append(
            (
                section_ordinal,
                current_section,
                ChartMeasure(
                    chords=tuple(label for label, _, _ in spans),
                    timestamp=timestamp,
                ),
                MeasureProjection(
                    measure_number=index,
                    start_frame=start,
                    end_frame=end,
                    section_name=current_section,
                    section_ordinal=section_ordinal,
                    chord_spans=tuple(spans),
                ),
            )
        )

    issues.append(
        PromotionIssue.create(
            "material",
            "timing.songchart_grid_is_display_only",
            (
                "SongChart 1.0.0 does not serialize the approved 4/4 frame grid; "
                "the exact mapping remains in the private promotion result."
            ),
            {"measure_count": len(measure_rows)},
        )
    )
    issues.append(
        PromotionIssue.create(
            "information",
            "provenance.retained_private",
            (
                "Inference, confidence, alternatives, media identity, and review "
                "lineage remain private and are not copied into SongChart."
            ),
        )
    )
    issues.append(
        PromotionIssue.create(
            "information",
            "guitar.sounding_notation",
            "Exported chord symbols are sounding chords; capo is performance setup only.",
            {"capo": config.capo},
        )
    )

    sections: list[ChartSection] = []
    active_section: tuple[int, str] | None = None
    active_bars: list[ChartMeasure] = []
    for ordinal, section_name, measure, _ in measure_rows:
        section_key = (ordinal, section_name)
        if active_section is not None and section_key != active_section:
            sections.append(
                ChartSection(name=active_section[1], bars=tuple(active_bars))
            )
            active_bars = []
        active_section = section_key
        active_bars.append(measure)
    if active_section is not None:
        sections.append(ChartSection(name=active_section[1], bars=tuple(active_bars)))

    notes = [
        "Chord symbols are sounding harmony; capo does not transpose the printed labels.",
        "Exact reviewed frame timing remains in the private promotion result.",
    ]
    for decision in config.guitar_decisions:
        if decision.note:
            notes.append(f"{decision.chord}: {decision.note}")
    chart = SongChart(
        title=config.title,
        artist=config.artist,
        key=config.key,
        tuning=config.tuning,
        capo=config.capo,
        version=config.chart_version,
        sections=tuple(sections),
        voicing_notes=tuple(notes),
    )
    return PromotionDraft(
        chart=chart,
        issues=tuple(
            sorted(issues, key=lambda item: (item.severity, item.code, item.id))
        ),
        timing_map=tuple(item[3] for item in measure_rows),
    )


def _guitar_issues(
    decisions: dict[str, GuitarDecision], issues: list[PromotionIssue]
) -> None:
    for chord, decision in sorted(decisions.items()):
        if "/" in chord and not decision.inversion_reviewed:
            raise PromotionError(
                "inversion_unreviewed",
                f"Confirm the bass/inversion for {chord} before approval.",
            )
        shape = get_chord_shape(chord)
        if shape is None:
            if decision.voicing != "manual_required":
                raise PromotionError(
                    "voicing_unavailable",
                    f"{chord} has no built-in guitar shape; record a manual decision.",
                )
            issues.append(
                PromotionIssue.create(
                    "material",
                    "guitar.no_builtin_shape",
                    "No built-in standard-tuning shape is available for a reviewed chord.",
                    {"chord": chord},
                )
            )
        elif decision.voicing != "built_in":
            raise PromotionError(
                "voicing_choice_unrepresentable",
                (
                    f"{chord} has a built-in exported diagram. SongChart 1.0.0 "
                    "cannot persist a different preferred voicing."
                ),
            )
        if decision.playability == "needs_adjustment":
            raise PromotionError(
                "playability_unresolved",
                (
                    f"Resolve the playability adjustment for {chord} before "
                    "approving a guitar-aware chart."
                ),
            )


def _export_label(state: str, label: str | None) -> str:
    if state == "no_chord":
        return "N.C."
    if state != "chord" or label is None:
        raise PromotionError(
            "review_unresolved",
            "Unknown or unresolved review segments cannot be promoted.",
        )
    match = re.fullmatch(r"([A-G](?:#|b)?):(maj|min)(/[A-G](?:#|b)?)?", label)
    if match:
        root, quality, bass = match.groups()
        suffix = "" if quality == "maj" else "m"
        return f"{root}{suffix}{bass or ''}"
    return label


def _timestamp(frame: int, sample_rate_hz: int) -> str:
    total_ms = (frame * 1_000 + sample_rate_hz // 2) // sample_rate_hz
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
