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
    source_url: https://youtube.com/example
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
"""
