# ChordAtlas Architecture

## Provenance Layer

The Provenance Layer is a first-class domain concern. Every meaningful chart
claim should be able to answer: "How do we know this?"

This applies to song metadata, chords, chord shapes, sections, measures, timing
markers, Roman numeral analysis, Nashville numbers, performance notes, recording
notes, difficulty ratings, voice-leading metrics, chord economy metrics,
confidence scores, and version history entries.

## Claim Origins

ChordAtlas separates a claim's origin from its verification status:

- `observed`: directly audible or visible from a source.
- `computed`: derived by software analysis.
- `inferred`: best estimate by a human or AI.
- `verified`: entered because a trusted reference confirms it.
- `user_entered`: manually supplied by the chart author.
- `unknown`: present in the chart, but origin is not yet documented.

`unknown` is allowed so incomplete charts can still load, but it is surfaced by
`SongChart.provenance_warnings()`.

## Verification Status

Verification status is normalized separately:

- `unverified`
- `verified`
- `disputed`
- `unknown`

Validation rules:

- Inferred claims must include confidence.
- Verified claims must include at least one evidence reference.
- Timestamp ranges must be valid and ordered.
- Confidence values are normalized to `low`, `medium`, or `high`.

## Data Model

`chordatlas.provenance` owns:

- `ProvenanceRecord`
- `EvidenceReference`
- `ProvenanceMixin`
- `Confidence`
- `SourceType`
- `ClaimOrigin`
- `VerificationStatus`

`chordatlas.models` applies provenance to current chart entities:

- `SongChart.provenance`
- `SongChart.metadata_provenance`
- `SongChart.chord_provenance`
- `SongChart.analysis_provenance`
- `SongChart.performance_note_provenance`
- `SongChart.recording_note_provenance`
- `ChordShape.provenance`
- `ChartSection.provenance`
- `ChartMeasure.provenance`
- `VersionEntry.provenance`

The first implementation keeps the existing simple YAML format valid. A bar can
still be written as:

```yaml
- [G, D/F#]
```

A provenance-aware measure can be written as:

```yaml
- chords: [Cadd9]
  timestamp: "00:13"
  provenance:
    source_type: audio
    timestamp_range: "00:13-00:18"
    method: human-ear transcription
    confidence: medium
    verification_status: unverified
    claim_origin: inferred
```

## Rendering Modes

Markdown and plain-text rendering support three provenance modes:

- `minimal`: hide provenance unless there is uncertainty.
- `standard`: show confidence, status, and compact evidence.
- `research`: show full source, method, contributor, notes, and evidence details.

JSON export always preserves complete provenance data.

## Design Constraint

Do not bury uncertainty. ChordAtlas should make strong charts possible, but
honest charts mandatory.
