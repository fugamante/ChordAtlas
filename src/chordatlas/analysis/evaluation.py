from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chordatlas.analysis.models import ChordCandidateTimeline
from chordatlas.media.models import FrameRange


@dataclass(frozen=True)
class ReferenceSegment:
    frame_range: FrameRange
    canonical_symbol: str


def evaluate_timeline(
    timeline: ChordCandidateTimeline,
    reference: tuple[ReferenceSegment, ...],
) -> dict[str, Any]:
    """Evaluate deterministic fixtures; this is not observed musician effort."""

    if not reference:
        raise ValueError("reference timeline is required")
    total = sum(item.frame_range.length_frames for item in reference)
    primary_correct = 0
    top_k_correct = 0
    unknown = 0
    brier_weighted = 0
    relabel = 0
    select_alternate = 0
    boundary_errors = []

    for expected in reference:
        expected_primary = 0
        expected_top_k = 0
        for predicted in timeline.segments:
            overlap = max(
                0,
                min(expected.frame_range.end_frame, predicted.frame_range.end_frame)
                - max(expected.frame_range.start_frame, predicted.frame_range.start_frame),
            )
            if overlap == 0:
                continue
            if predicted.state == "unknown" or not predicted.candidates:
                unknown += overlap
                brier_weighted += overlap * 1_000_000 * 1_000_000
                continue
            labels = [item.canonical_symbol for item in predicted.candidates]
            is_primary = labels[0] == expected.canonical_symbol
            is_top_k = expected.canonical_symbol in labels
            probability_ppm = predicted.candidates[0].confidence_ppm
            target_ppm = 1_000_000 if is_primary else 0
            primary_correct += overlap if is_primary else 0
            top_k_correct += overlap if is_top_k else 0
            expected_primary += overlap if is_primary else 0
            expected_top_k += overlap if is_top_k else 0
            brier_weighted += overlap * (probability_ppm - target_ppm) ** 2
        covered = sum(
            max(
                0,
                min(expected.frame_range.end_frame, item.frame_range.end_frame)
                - max(expected.frame_range.start_frame, item.frame_range.start_frame),
            )
            for item in timeline.segments
        )
        missing = expected.frame_range.length_frames - covered
        if missing:
            unknown += missing
            brier_weighted += missing * 1_000_000 * 1_000_000
        if expected_primary != expected.frame_range.length_frames:
            if expected_top_k == expected.frame_range.length_frames:
                select_alternate += 1
            else:
                relabel += 1

    predicted_boundaries = [item.frame_range.end_frame for item in timeline.segments[:-1]]
    expected_boundaries = [item.frame_range.end_frame for item in reference[:-1]]
    boundary_errors, unmatched_expected, unmatched_predicted = _align_boundaries(
        expected_boundaries,
        predicted_boundaries,
        timeline.analyzed_range.length_frames,
    )
    boundary_move = sum(error > 0 for error in boundary_errors)

    duration_milliseconds = max(
        timeline.analyzed_range.length_frames * 1_000
        // timeline.timebase.sample_rate,
        1,
    )
    actions = {
        "select_alternate": select_alternate,
        "relabel": relabel,
        "boundary_move": boundary_move,
        "split": max(len(reference) - len(timeline.segments), 0),
        "merge": max(len(timeline.segments) - len(reference), 0),
        "insert": 0,
        "delete": 0,
        "mark_no_chord": 0,
    }
    total_actions = sum(actions.values())
    return {
        "evaluation_schema_version": "1.0.0-draft",
        "scope": "deterministic_synthetic_fixture_only",
        "primary_duration_accuracy_ppm": primary_correct * 1_000_000 // total,
        "top_k_duration_accuracy_ppm": top_k_correct * 1_000_000 // total,
        "accepted_primary_duration_ppm": primary_correct * 1_000_000 // total,
        "unknown_duration_ppm": unknown * 1_000_000 // total,
        "boundary_mean_absolute_error_frames": (
            sum(boundary_errors) // len(boundary_errors) if boundary_errors else 0
        ),
        "boundary_max_absolute_error_frames": max(boundary_errors, default=0),
        "boundary_unmatched_count": unmatched_expected + unmatched_predicted,
        "confidence_brier_ppm": brier_weighted // total // 1_000_000,
        "heuristic_correction_flags": actions,
        "heuristic_correction_flags_per_minute_milli": (
            total_actions * 60_000_000 // duration_milliseconds
        ),
        "caveat": (
            "Synthetic heuristic only, not a minimum edit script or effort estimate; "
            "Stage 3 must measure observed time and actions to reviewed export."
        ),
    }


def _align_boundaries(
    expected: list[int],
    predicted: list[int],
    duration_frames: int,
) -> tuple[list[int], int, int]:
    """Globally align positions; label substitutions do not invent timing errors."""

    if not expected or not predicted:
        return [], len(expected), len(predicted)
    if len(expected) * len(predicted) > 1_000_000:
        return _align_boundaries_greedy(expected, predicted)

    gap_cost = duration_frames + 1
    previous = [index * gap_cost for index in range(len(predicted) + 1)]
    choices: list[bytearray] = [bytearray(len(predicted) + 1)]
    for expected_index, expected_frame in enumerate(expected, start=1):
        current = [expected_index * gap_cost]
        row = bytearray(len(predicted) + 1)
        row[0] = 1
        for predicted_index, predicted_frame in enumerate(predicted, start=1):
            diagonal = previous[predicted_index - 1] + abs(expected_frame - predicted_frame)
            skip_expected = previous[predicted_index] + gap_cost
            skip_predicted = current[predicted_index - 1] + gap_cost
            best = min(diagonal, skip_expected, skip_predicted)
            current.append(best)
            row[predicted_index] = (
                0 if diagonal == best else 1 if skip_expected == best else 2
            )
        previous = current
        choices.append(row)

    errors: list[int] = []
    expected_index = len(expected)
    predicted_index = len(predicted)
    unmatched_expected = 0
    unmatched_predicted = 0
    while expected_index or predicted_index:
        choice = choices[expected_index][predicted_index] if expected_index else 2
        if choice == 0:
            errors.append(
                abs(expected[expected_index - 1] - predicted[predicted_index - 1])
            )
            expected_index -= 1
            predicted_index -= 1
        elif choice == 1:
            unmatched_expected += 1
            expected_index -= 1
        else:
            unmatched_predicted += 1
            predicted_index -= 1
    errors.reverse()
    return errors, unmatched_expected, unmatched_predicted


def _align_boundaries_greedy(
    expected: list[int],
    predicted: list[int],
) -> tuple[list[int], int, int]:
    errors: list[int] = []
    expected_index = 0
    predicted_index = 0
    unmatched_expected = 0
    unmatched_predicted = 0
    while expected_index < len(expected) and predicted_index < len(predicted):
        expected_remaining = len(expected) - expected_index
        predicted_remaining = len(predicted) - predicted_index
        if predicted_remaining > expected_remaining and (
            predicted_index + 1 < len(predicted)
            and abs(expected[expected_index] - predicted[predicted_index + 1])
            <= abs(expected[expected_index] - predicted[predicted_index])
        ):
            predicted_index += 1
            unmatched_predicted += 1
            continue
        if expected_remaining > predicted_remaining and (
            expected_index + 1 < len(expected)
            and abs(expected[expected_index + 1] - predicted[predicted_index])
            < abs(expected[expected_index] - predicted[predicted_index])
        ):
            expected_index += 1
            unmatched_expected += 1
            continue
        errors.append(abs(expected[expected_index] - predicted[predicted_index]))
        expected_index += 1
        predicted_index += 1
    return (
        errors,
        unmatched_expected + len(expected) - expected_index,
        unmatched_predicted + len(predicted) - predicted_index,
    )
