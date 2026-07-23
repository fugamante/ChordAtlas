from __future__ import annotations

import math
import struct
import sys
import wave
from array import array
from collections.abc import Callable
from pathlib import Path

from chordatlas.analysis.models import (
    AnalysisError,
    AnalysisSpec,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    KeyHypothesis,
    TempoHypothesis,
)
from chordatlas.media.models import FrameRange

MAX_ANALYSIS_SECONDS = 10 * 60
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MAJOR_PROFILE = (636, 223, 348, 233, 438, 409, 252, 519, 239, 366, 229, 288)
_MINOR_PROFILE = (633, 268, 352, 538, 260, 353, 254, 475, 398, 269, 334, 317)


def analyze_pcm16_wav(
    path: Path,
    spec: AnalysisSpec,
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> ChordCandidateTimeline:
    """Run the deterministic, deliberately modest baseline-v1 hypothesis engine."""

    parameters = dict(spec.config.parameters)
    target_rate = parameters["target_rate"]
    window_frames = parameters["window_frames"]
    hop_frames = parameters["hop_frames"]
    selected = spec.analyzed_range
    if selected.length_frames > spec.timebase.sample_rate * MAX_ANALYSIS_SECONDS:
        raise AnalysisError(
            "analysis_range_too_long",
            "The baseline engine accepts at most ten minutes per run. Analyze a shorter loop.",
        )
    samples, analysis_rate = _read_downmixed(
        path,
        selected,
        target_rate=target_rate,
        cancelled=cancelled,
    )
    if cancelled():
        raise AnalysisError("analysis_cancelled", "Analysis was cancelled.")
    if len(samples) < max(window_frames // 4, 64):
        raise AnalysisError(
            "analysis_range_too_short",
            "Choose at least a quarter-second analysis range.",
        )

    windows = _window_chroma(
        samples,
        analysis_rate=analysis_rate,
        window_frames=window_frames,
        hop_frames=hop_frames,
        cancelled=cancelled,
    )
    if not windows:
        return ChordCandidateTimeline.create(
            spec_id=spec.id,
            timebase=spec.timebase,
            analyzed_range=selected,
            result_kind="no_candidates",
            beats=(),
            tempo_hypotheses=(),
            key_hypotheses=(),
            segments=(),
            engine=spec.config.engine,
        )

    max_energy = max(item[0] for item in windows)
    labels = [_classify_chord(chroma, energy, max_energy) for energy, chroma in windows]
    segments = _merge_segments(
        labels,
        selected=selected,
        analysis_rate=analysis_rate,
        hop_frames=hop_frames,
    )
    aggregate = [sum(item[1][pitch] for item in windows) for pitch in range(12)]
    keys = _key_hypotheses(aggregate)
    tempo, beats = _tempo_and_beats(
        [item[0] for item in windows],
        selected=selected,
        source_rate=spec.timebase.sample_rate,
        analysis_rate=analysis_rate,
        hop_frames=hop_frames,
        min_bpm=parameters["min_bpm"],
        max_bpm=parameters["max_bpm"],
    )
    return ChordCandidateTimeline.create(
        spec_id=spec.id,
        timebase=spec.timebase,
        analyzed_range=selected,
        result_kind="candidates",
        beats=beats,
        tempo_hypotheses=(() if tempo is None else (tempo,)),
        key_hypotheses=keys,
        segments=segments,
        engine=spec.config.engine,
    )


def _read_downmixed(
    path: Path,
    selected: FrameRange,
    *,
    target_rate: int,
    cancelled: Callable[[], bool],
) -> tuple[array[float], int]:
    try:
        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            source_rate = reader.getframerate()
            if reader.getsampwidth() != 2 or channels not in (1, 2):
                raise AnalysisError("unsupported_media", "The baseline engine requires PCM16 WAV.")
            reader.setpos(selected.start_frame)
            decimation = max(round(source_rate / target_rate), 1)
            analysis_rate = max(source_rate // decimation, 1)
            output = array("f")
            remaining = selected.length_frames
            carry: list[int] = []
            while remaining:
                if cancelled():
                    raise AnalysisError("analysis_cancelled", "Analysis was cancelled.")
                requested = min(remaining, 16_384)
                raw = reader.readframes(requested)
                values = array("h")
                values.frombytes(raw)
                if sys.byteorder != "little":
                    values.byteswap()
                if len(values) != requested * channels:
                    raise AnalysisError("decode_failed", "The authorized WAV ended unexpectedly.")
                if channels == 2:
                    mono = [
                        (values[index] + values[index + 1]) // 2
                        for index in range(0, len(values), 2)
                    ]
                else:
                    mono = list(values)
                carry.extend(mono)
                complete = len(carry) // decimation
                for index in range(complete):
                    start = index * decimation
                    output.append(sum(carry[start : start + decimation]) / decimation)
                del carry[: complete * decimation]
                remaining -= requested
            if carry:
                output.append(sum(carry) / len(carry))
    except AnalysisError:
        raise
    except (EOFError, OSError, wave.Error, struct.error):
        raise AnalysisError("decode_failed", "The authorized WAV could not be analyzed.") from None
    return output, analysis_rate


def _window_chroma(
    samples: array[float],
    *,
    analysis_rate: int,
    window_frames: int,
    hop_frames: int,
    cancelled: Callable[[], bool],
) -> list[tuple[int, tuple[int, ...]]]:
    if window_frames & (window_frames - 1):
        raise AnalysisError("invalid_parameters", "Window size must be a power of two.", retryable=False)
    hann = [0.5 - 0.5 * math.cos(2 * math.pi * index / window_frames) for index in range(window_frames)]
    windows: list[tuple[int, tuple[int, ...]]] = []
    start = 0
    while start < len(samples):
        if cancelled():
            raise AnalysisError("analysis_cancelled", "Analysis was cancelled.")
        frame = [0j] * window_frames
        actual = min(window_frames, len(samples) - start)
        energy_sum = 0
        for index in range(actual):
            sample = samples[start + index]
            energy_sum += int(sample * sample)
            frame[index] = complex(sample * hann[index], 0)
        _fft_in_place(frame)
        chroma = [0] * 12
        upper = min(window_frames // 2, int(1100 * window_frames / analysis_rate))
        lower = max(1, int(65 * window_frames / analysis_rate))
        for bin_index in range(lower, upper + 1):
            frequency = bin_index * analysis_rate / window_frames
            midi = round(69 + 12 * math.log2(frequency / 440))
            if 36 <= midi <= 83:
                magnitude = int(abs(frame[bin_index]))
                chroma[midi % 12] += magnitude
        energy = int(math.isqrt(max(energy_sum // max(actual, 1), 0)))
        windows.append((energy, tuple(chroma)))
        start += hop_frames
    return windows


def _fft_in_place(values: list[complex]) -> None:
    size = len(values)
    target = 0
    for index in range(1, size):
        bit = size >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if index < target:
            values[index], values[target] = values[target], values[index]
    length = 2
    while length <= size:
        angle = -2 * math.pi / length
        root = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, size, length):
            factor = 1 + 0j
            for offset in range(half):
                even = values[start + offset]
                odd = values[start + offset + half] * factor
                values[start + offset] = even + odd
                values[start + offset + half] = even - odd
                factor *= root
        length *= 2


def _classify_chord(
    chroma: tuple[int, ...],
    energy: int,
    max_energy: int,
) -> tuple[str, tuple[ChordCandidate, ...], int]:
    total = sum(chroma)
    if max_energy <= 0 or energy * 20 < max_energy or total <= 0:
        candidate = ChordCandidate("N.C.", "N.C.", 1, 900_000)
        return "no_chord", (candidate,), 900_000
    scored: list[tuple[int, str]] = []
    for root in range(12):
        for quality, third in (("maj", 4), ("min", 3)):
            score = 5 * chroma[root] + 3 * chroma[(root + third) % 12] + 3 * chroma[(root + 7) % 12]
            off = total - chroma[root] - chroma[(root + third) % 12] - chroma[(root + 7) % 12]
            scored.append((score - off, f"{_NOTE_NAMES[root]}:{quality}"))
    scored.sort(reverse=True)
    best = scored[0][0]
    spread = max(best - scored[2][0], 0)
    confidence = min(950_000, max(200_000, 350_000 + spread * 600_000 // max(abs(best), 1)))
    candidates = []
    for rank, (_score, label) in enumerate(scored[:3], 1):
        item_confidence = max(50_000, confidence - (rank - 1) * 180_000)
        candidates.append(ChordCandidate(label, label, rank, item_confidence))
    return "chord", tuple(candidates), max(0, 1_000_000 - confidence)


def _merge_segments(
    labels: list[tuple[str, tuple[ChordCandidate, ...], int]],
    *,
    selected: FrameRange,
    analysis_rate: int,
    hop_frames: int,
) -> tuple[ChordSegment, ...]:
    source_per_hop_num = hop_frames * selected.length_frames
    analysis_length = max(len(labels) * hop_frames, 1)
    boundaries = [
        selected.start_frame + min(source_per_hop_num * index // analysis_length, selected.length_frames)
        for index in range(len(labels) + 1)
    ]
    boundaries[-1] = selected.end_frame
    groups: list[tuple[int, int, tuple[str, tuple[ChordCandidate, ...], int]]] = []
    group_start = 0
    for index in range(1, len(labels) + 1):
        if index == len(labels) or _label_key(labels[index]) != _label_key(labels[group_start]):
            groups.append((group_start, index, labels[group_start]))
            group_start = index
    segments = []
    for ordinal, (start_index, end_index, item) in enumerate(groups):
        start = boundaries[start_index]
        end = boundaries[end_index]
        if end <= start:
            continue
        state, candidates, no_chord = item
        segments.append(
            ChordSegment(
                ordinal=len(segments),
                frame_range=FrameRange(start, end),
                state=state,
                candidates=candidates,
                no_chord_probability_ppm=no_chord,
            )
        )
    if not segments:
        return (
            ChordSegment(
                ordinal=0,
                frame_range=selected,
                state="unknown",
                candidates=(),
                no_chord_probability_ppm=0,
            ),
        )
    if segments[-1].frame_range.end_frame != selected.end_frame:
        last = segments[-1]
        segments[-1] = ChordSegment(
            ordinal=last.ordinal,
            frame_range=FrameRange(last.frame_range.start_frame, selected.end_frame),
            state=last.state,
            candidates=last.candidates,
            no_chord_probability_ppm=last.no_chord_probability_ppm,
        )
    return tuple(segments)


def _label_key(
    item: tuple[str, tuple[ChordCandidate, ...], int],
) -> tuple[str, str | None]:
    return item[0], item[1][0].canonical_symbol if item[1] else None


def _key_hypotheses(chroma: list[int]) -> tuple[KeyHypothesis, ...]:
    scored = []
    for root in range(12):
        major = sum(chroma[(root + index) % 12] * weight for index, weight in enumerate(_MAJOR_PROFILE))
        minor = sum(chroma[(root + index) % 12] * weight for index, weight in enumerate(_MINOR_PROFILE))
        scored.extend(((major, f"{_NOTE_NAMES[root]} major"), (minor, f"{_NOTE_NAMES[root]} minor")))
    scored.sort(reverse=True)
    best = scored[0][0]
    confidence = 0 if best <= 0 else min(900_000, 300_000 + (best - scored[1][0]) * 600_000 // best)
    return tuple(
        KeyHypothesis(label, max(50_000, confidence - rank * 180_000))
        for rank, (_score, label) in enumerate(scored[:3])
    )


def _tempo_and_beats(
    energy: list[int],
    *,
    selected: FrameRange,
    source_rate: int,
    analysis_rate: int,
    hop_frames: int,
    min_bpm: int,
    max_bpm: int,
) -> tuple[TempoHypothesis | None, tuple[int, ...]]:
    if len(energy) < 4:
        return None, ()
    onset = [0] + [max(energy[index] - energy[index - 1], 0) for index in range(1, len(energy))]
    hops_per_second_num = analysis_rate
    best_lag = 0
    best_score = 0
    min_lag = max(1, round(60 * hops_per_second_num / (max_bpm * hop_frames)))
    max_lag = max(min_lag, round(60 * hops_per_second_num / (min_bpm * hop_frames)))
    for lag in range(min_lag, min(max_lag, len(onset) - 1) + 1):
        score = sum(onset[index] * onset[index - lag] for index in range(lag, len(onset)))
        if score > best_score:
            best_lag, best_score = lag, score
    if best_lag == 0 or best_score == 0:
        return None, ()
    bpm_milli = round(60_000 * analysis_rate / (best_lag * hop_frames))
    phase = max(range(min(best_lag, len(onset))), key=onset.__getitem__)
    beat_frames = []
    for window_index in range(phase, len(onset), best_lag):
        frame = selected.start_frame + round(window_index * hop_frames * source_rate / analysis_rate)
        if frame < selected.end_frame:
            beat_frames.append(frame)
    confidence = min(900_000, 250_000 + best_score * 650_000 // max(sum(value * value for value in onset), 1))
    return TempoHypothesis(bpm_milli, confidence), tuple(sorted(set(beat_frames)))
