from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from chordatlas.models import SongChart


def load_song_chart(path: str | Path) -> SongChart:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        data: Any = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {source}")
    return SongChart.from_mapping(data)


def song_chart_to_json(chart: SongChart, *, indent: int = 2) -> str:
    return json.dumps(chart.to_mapping(), indent=indent, sort_keys=True) + "\n"


def example_song_yaml() -> str:
    return """title: Example Open-String Progression
artist: ChordAtlas
key: G
tuning: Standard
capo: None
version: "2.0"
confidence: Medium
provenance:
  source_type: contributor
  source_name: Starter chart
  method: user-entered
  contributor: ChordAtlas Maintainers
  confidence: medium
  verification_status: unverified
  claim_origin: user_entered
chord_provenance:
  Cadd9:
    source_type: audio
    source_name: Example recording
    source_url: https://example.invalid/recording
    timestamp_range: 00:13-00:18
    method: human-ear transcription
    contributor: Example Contributor
    confidence: medium
    verification_status: unverified
    claim_origin: inferred
    notes: Chord sounds like Cadd9, but Cmaj7 is possible.
version_history:
  - version: "1.0"
    changes:
      - Initial transcription.
  - version: "1.1"
    changes:
      - Corrected Verse 2.
      - Changed Cmaj7 to Cadd9.
  - version: "1.2"
    changes:
      - Added alternate capo version.
      - Improved Guitar 2 voicings.
  - version: "2.0"
    changes:
      - Verified against isolated stems.
sections:
  - name: Intro
    bars:
      - [G]
      - [D/F#]
      - [Em7]
      - [Cadd9]
    repeat: "x2"
  - name: Verse
    bars:
      - [G]
      - [D/F#]
      - [Em7]
      - [Cadd9]
analysis:
  roman:
    - "I"
    - "V6"
    - "vi7"
    - "IVadd9"
  nashville:
    - "1"
    - "5/7"
    - "6m7"
    - "4add9"
performance_notes:
  - Use ringing open-string voicings when possible.
  - Prefer playable guitar shapes over piano-style harmonic spellings.
  - Mark uncertain chords clearly.
recordings:
  studio:
    title: Example recording
    source_url: https://example.invalid/recording
    version_label: Studio reference
  stems:
    title: Example isolated stems
    version_label: Stem reference
  live:
    title: Example live performance
    source_url: https://example.invalid/live
    version_label: Live reference
  demo:
    title: Example acoustic demo
    version_label: Demo reference
  remaster:
    title: Example remaster
    source_url: https://example.invalid/remaster
    version_label: Remaster reference
structured_recording_notes:
  Guitar 1:
    category: instrumentation
    recording_ids: [studio, stems]
    notes:
      - text: Left channel
        claim_origin: observed
        confidence: high
      - text: Acoustic
        claim_origin: observed
        confidence: high
      - text: Open voicings
        claim_origin: observed
        confidence: medium
  Guitar 2:
    category: instrumentation
    severity: medium
    recording_ids: [studio]
    notes:
      - text: Right channel
        claim_origin: observed
        confidence: high
      - text: Electric
        claim_origin: observed
        confidence: high
      - text: Octave doubling
        claim_origin: inferred
        confidence: medium
        severity: medium
  Bass:
    category: performance
    severity: low
    recording_ids: [studio, live, remaster]
    notes:
      - text: Walks to D/F#
        claim_origin: observed
        confidence: medium
  Effects:
    category: effects
    notes:
      - text: Light chorus
        claim_origin: inferred
        confidence: medium
        recording_id: studio
      - text: Spring reverb
        claim_origin: inferred
        confidence: medium
        recording_id: studio
      - text: Slight tape saturation
        claim_origin: inferred
        confidence: low
        severity: low
        recording_id: studio
      - text: Brighter high-end EQ
        claim_origin: inferred
        confidence: medium
        severity: medium
        recording_id: remaster
  Estimated tuning:
    category: tuning
    notes:
      - text: Standard, approximately 15 cents flat
        claim_origin: inferred
        confidence: medium
        severity: high
        recording_id: studio
        provenance:
          source_type: audio
          source_name: Example recording
          method: tuner comparison
          confidence: medium
          verification_status: unverified
          claim_origin: inferred
      - text: Standard concert pitch
        claim_origin: inferred
        confidence: medium
        severity: high
        recording_id: remaster
  Arrangement:
    category: arrangement
    notes:
      - text: Stripped-down single acoustic guitar
        claim_origin: observed
        confidence: medium
        severity: medium
        recording_id: demo
      - text: Extended outro vamp
        claim_origin: observed
        confidence: medium
        severity: low
        recording_id: live
  Mix:
    category: mix
    notes:
      - text: Wide acoustic/electric split
        claim_origin: observed
        confidence: medium
        severity: medium
        recording_id: studio
      - text: Vocal-forward balance
        claim_origin: inferred
        confidence: medium
        severity: medium
        recording_id: remaster
  Source quality:
    category: source_quality
    notes:
      - text: Isolated guitar detail available
        claim_origin: observed
        confidence: high
        recording_id: stems
      - text: Audience ambience masks low-level parts
        claim_origin: observed
        confidence: medium
        severity: medium
        recording_id: live
"""
