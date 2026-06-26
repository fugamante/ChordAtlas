# ChordAtlas

ChordAtlas is a CLI-first project for building clean, structured guitar chord charts from song data. The initial version focuses on hand-authored YAML input, built-in common guitar chord shapes, and Markdown/plain-text export.

It intentionally avoids storing or reproducing copyrighted lyrics. Charts should capture chord names, section structure, timing/performance notes, and analysis metadata.

## Current Features

- Structured `SongChart` and `ChordShape` data models.
- First-class provenance records for documenting how chart claims are known.
- Built-in guitar chord-shape dictionary for common open-position and basic barre chords.
- Automatic chord reference generation for only the chords used in a chart.
- Structured version history with Markdown and plain-text rendering.
- Markdown, plain-text, and JSON export.
- JSON Schema contract for the normalized `SongChart` export.
- `chordchart` CLI with `new`, `render`, and `validate` commands.
- Example YAML chart and focused tests.

## Install For Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Usage

Create a starter chart:

```bash
chordchart new song.yaml
```

Render Markdown:

```bash
chordchart render examples/open-string-progression.yaml --format md
```

Render plain text:

```bash
chordchart render examples/open-string-progression.yaml --format txt
```

Render JSON, preserving complete provenance data:

```bash
chordchart render examples/open-string-progression.yaml --format json
```

The canonical machine-readable export contract is
[schemas/song-chart.schema.json](schemas/song-chart.schema.json).

Validate a chart and surface provenance warnings:

```bash
chordchart validate examples/open-string-progression.yaml
```

Show provenance in rendered Markdown or text:

```bash
chordchart render examples/open-string-progression.yaml --format md --provenance standard
chordchart render examples/open-string-progression.yaml --format md --provenance research
```

## YAML Shape

```yaml
title: Example Open-String Progression
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
analysis:
  roman:
    - "I"
    - "V6"
    - "vi7"
    - "IVadd9"
performance_notes:
  - Use ringing open-string voicings when possible.
  - Mark uncertain chords clearly.
```

## Provenance

Every meaningful chart claim should be able to answer: "How do we know this?"
This includes chords, voicings, tuning, capo, key, tempo, section labels,
recording notes, performance notes, confidence scores, and harmonic analysis.

ChordAtlas distinguishes claim origins:

- `observed`: directly audible or visible from a source.
- `computed`: derived by software analysis.
- `inferred`: best estimate by a human or automated process. Must include confidence.
- `verified`: confirmed against a trusted reference.
- `user_entered`: manually supplied by the chart author.
- `unknown`: present in the chart, but origin is not yet documented. Allowed, but flagged.

Each provenance record can cite:

- `source_type`: `audio`, `video`, `stem`, `score`, `tab`, `liner_notes`,
  `interview`, `software`, `contributor`, `reference`, or `unknown`.
- `source_name` and `source_url`.
- `timestamp_range`, such as `00:13-00:18`.
- `method`, such as `human-ear transcription`, `spectral analysis`, or
  `official score comparison`.
- `contributor`.
- `confidence`: `low`, `medium`, or `high`.
- `verification_status`: `unverified`, `verified`, `disputed`, or `unknown`.
- `evidence_refs`: concrete evidence IDs, URLs, or timestamp references.
- `notes`: any uncertainty or alternate interpretation.

Contributor rule: do not bury uncertainty. If a chord, voicing, tuning, key,
performance note, or analysis claim is inferred, mark it as inferred and include
confidence. If a claim is verified, cite at least one evidence reference.

## Design Notes

The core package is renderer-agnostic:

- `chordatlas.models` defines the chart and chord data structures.
- `chordatlas.provenance` defines evidence, confidence, source, and verification primitives.
- `chordatlas.chords` owns built-in chord-shape lookup.
- `chordatlas.render` turns structured charts into Markdown or plain text.
- `chordatlas.io` loads YAML and creates starter examples.
- `chordatlas.cli` exposes the `chordchart` command.

This keeps the first version small while leaving room for later ingestion, detection, analysis, web UI, and PDF export layers.

## Validation

```bash
pytest
```

Schema contract tests use `jsonschema` from the development dependency set and
validate exported JSON, not hand-authored YAML.

## Roadmap

ChordAtlas grows in capability layers. The active product goal is the
Foundation Gate: a professional CLI that can produce clean, versioned,
provenance-aware chord charts from structured source files.

See [docs/roadmap.md](docs/roadmap.md) for the full layered roadmap.

Near-term candidates after the Foundation Gate:

- User-defined chord-shape dictionaries.
- JSON import.
- Capo and transposition helpers.
- Roman numeral and Nashville analysis helpers.
- Structured recording notes sections:

```text
═══════════════════════════════════════
RECORDING NOTES
═══════════════════════════════════════

Guitar 1
✓ Left channel
✓ Acoustic
✓ Open voicings

Guitar 2
✓ Right channel
✓ Electric
✓ Octave doubling

Bass
✓ Walks to D/F#

Effects
✓ Light chorus
✓ Spring reverb
✓ Slight tape saturation

Estimated tuning
Standard, approximately 15 cents flat
```
