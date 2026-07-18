# ChordAtlas

ChordAtlas is a CLI-first project for building clean, structured guitar chord charts from song data. The initial version focuses on hand-authored YAML input, built-in common guitar chord shapes, and Markdown/plain-text export.

It intentionally avoids storing or reproducing copyrighted lyrics. Charts should capture chord names, section structure, timing/performance notes, and analysis metadata.

## Current Features

- Structured `SongChart` and `ChordShape` data models.
- First-class provenance records for documenting how chart claims are known.
- Built-in guitar chord-shape dictionary for common open-position and basic barre chords.
- Automatic chord reference generation for only the chords used in a chart.
- Structured version history with Markdown and plain-text rendering.
- Structured recording notes for grouped parts, effects, tuning observations, and multiple sources.
- Markdown, plain-text, JSON, and recording-comparison analytics export.
- JSON Schema contract for the normalized `SongChart` export.
- `chordchart` CLI with `new`, `render`, `compare`, `validate`, `schemas`,
  `snapshots`, and `release-check` commands.
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

Export derived recording-source comparison analytics:

```bash
chordchart compare examples/open-string-progression.yaml --format json
chordchart compare examples/open-string-progression.yaml --format md
chordchart compare examples/open-string-progression.yaml --format txt
chordchart compare examples/open-string-progression.yaml --format csv
```

The JSON comparison export includes category totals, shared/source-specific
claim counts, per-recording counts, severity counts, and detailed
source-specific claims.

Comparison exports are compact by default. Add provenance when the comparison
needs to show how recording claims are known:

```bash
chordchart compare examples/open-string-progression.yaml --format json --provenance standard
chordchart compare examples/open-string-progression.yaml --format csv --provenance research
```

`standard` adds confidence, claim origin, provenance summaries, recording source
URLs, and version labels. `research` also includes full provenance records and
evidence references in JSON, and evidence reference IDs in extended CSV.

For CSV workflows that need the full research evidence model, write a JSON
sidecar:

```bash
chordchart compare examples/research-recording-comparison.yaml \
  --format csv \
  --provenance research \
  --category effects \
  --recording remaster \
  --severity medium \
  --source-specific-only \
  --metadata-json research-comparison.metadata.json
```

The CSV still prints to stdout. The sidecar records the filters used, schema
versions, CSV columns, recording source metadata, full provenance records,
evidence references, and the research comparison payload.

Filter comparison output for review or spreadsheet workflows:

```bash
chordchart compare examples/open-string-progression.yaml --format csv \
  --category effects \
  --recording remaster \
  --severity medium \
  --source-specific-only
```

Filters can be repeated. Category and severity values remain extensible strings;
recording filters must match declared recording IDs.

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
```

## Recording Notes

Use `structured_recording_notes` for grouped arrangement and production notes.
The compact mapping form is easiest for hand-authored YAML:

```yaml
structured_recording_notes:
  Guitar 1:
    - Left channel
    - Acoustic
    - Open voicings
  Guitar 2:
    - Right channel
    - Electric
    - Octave doubling
  Bass:
    - Walks to D/F#
  Effects:
    - Light chorus
    - Spring reverb
    - Slight tape saturation
  Estimated tuning: Standard, approximately 15 cents flat
```

Use mapping entries when a note needs observed/estimated semantics, confidence,
provenance, or recording-specific scope:

```yaml
recordings:
  studio:
    title: Example recording
    version_label: Studio reference
  live:
    title: Example live performance
structured_recording_notes:
  Guitar 1:
    category: instrumentation
    recording_ids: [studio]
    notes:
      - text: Left channel
        claim_origin: observed
        confidence: high
  Effects:
    category: effects
    notes:
      - text: Slight tape saturation
        claim_origin: inferred
        confidence: low
        severity: low
        recording_id: studio
  Estimated tuning:
    category: tuning
    recording_ids: [studio, live]
    value:
      text: Standard, approximately 15 cents flat
      claim_origin: inferred
      confidence: medium
      provenance:
        source_type: audio
        method: tuner comparison
        confidence: medium
        verification_status: unverified
        claim_origin: inferred
```

The explicit list form is equivalent and is accepted when a stable array shape is
more convenient:

```yaml
structured_recording_notes:
  - name: Guitar 1
    notes:
      - text: Left channel
        claim_origin: observed
        confidence: high
      - Acoustic
  - name: Estimated tuning
    value:
      text: Standard, approximately 15 cents flat
      claim_origin: inferred
      confidence: medium
```

Minimal rendering shows only note text. `--provenance standard` adds
observed/estimated and confidence labels, while `--provenance research` also
includes full provenance fields. When `recordings` and scoped notes are present,
renderers also add a `Recording Source Comparison` section that groups claims by
category first, then source, and lists source-specific differences. Recommended
categories are `tuning`, `instrumentation`, `effects`, `arrangement`,
`performance`, `mix`, and `source_quality`; custom category strings are allowed.
Optional `severity` labels such as `low`, `medium`, and `high` are also allowed
and are summarized in comparison output.
The older flat `recording_notes` list is still supported for compatibility.

For a denser research fixture with multiple recordings, source URLs, verified
evidence references, inferred claims, severity labels, and useful CSV sidecar
filters, see
[examples/research-recording-comparison.yaml](examples/research-recording-comparison.yaml).

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

## JSON Contract

YAML is the human-authored format. The normalized machine-readable preservation
format is JSON:

```bash
chordchart render examples/open-string-progression.yaml --format json
```

Every JSON export includes `schema_version`. The current contract is `1.0.0`.
Canonical schemas live in the package resources:

- `src/chordatlas/schemas/song-chart.schema.json`
- `src/chordatlas/schemas/provenance-record.schema.json`
- `src/chordatlas/schemas/recording-comparison.schema.json`
- `src/chordatlas/schemas/recording-comparison-metadata.schema.json`

The repo-root `schemas/` directory is a generated mirror for documentation,
review, and source-tree fallback:

- `schemas/song-chart.schema.json`
- `schemas/provenance-record.schema.json`
- `schemas/recording-comparison.schema.json`
- `schemas/recording-comparison-metadata.schema.json`

Recording comparison analytics are a separate derived export:

```bash
chordchart compare examples/open-string-progression.yaml --format json
chordchart compare examples/open-string-progression.yaml --format csv --source-specific-only
chordchart compare examples/research-recording-comparison.yaml \
  --format csv \
  --provenance research \
  --metadata-json research-comparison.metadata.json
```

The comparison contract uses `comparison_schema_version`. Its current value is
`1.0.0`. Categories and severity labels are strings by design so projects can
extend them without a schema migration. Provenance-aware comparison fields are
optional and only appear when `chordchart compare --provenance standard` or
`--provenance research` is used.

CSV sidecars use `metadata_schema_version`. Its current value is `1.0.0`.
`--metadata-json` is supported only with `--format csv --provenance research`.

Check or sync the mirror:

```bash
chordchart schemas --check
chordchart schemas --sync
```

Schema update workflow:

1. Edit the canonical files in `src/chordatlas/schemas/`.
2. Run `chordchart schemas --sync`.
3. Run `chordchart schemas --check`.
4. Run `pytest`.

Installed CLI validation reads `chordatlas.schemas` first, so it does not depend
on running from a source checkout.

Compatibility policy:

- Additive optional fields are minor-compatible changes.
- Removing fields, renaming fields, changing types, changing enum values, or
  changing required-field semantics requires a major contract update.
- JSON export must preserve full provenance data.
- Markdown and plain-text defaults are protected by golden snapshots.
- JSON export shape is protected by a golden snapshot and schema validation.

## Design Notes

The core package is renderer-agnostic:

- `chordatlas.models` defines the chart and chord data structures.
- `chordatlas.provenance` defines evidence, confidence, source, and verification primitives.
- `chordatlas.chords` owns built-in chord-shape lookup.
- `chordatlas.render` turns structured charts into Markdown or plain text.
- `chordatlas.compare` derives recording-source comparison analytics, applies
  optional filters, and renders comparison-focused JSON, Markdown, plain text,
  or CSV.
- `chordatlas.io` loads YAML and creates starter examples.
- `chordatlas.cli` exposes the `chordchart` command.

This keeps the first version small while leaving room for later ingestion, detection, analysis, web UI, and PDF export layers.

## Validation

```bash
pytest
```

Schema contract tests use `jsonschema` from the development dependency set and
validate exported JSON, not hand-authored YAML.

Validate a chart from the CLI:

```bash
chordchart validate examples/open-string-progression.yaml
```

`chordchart validate` always verifies that input can be parsed into the
ChordAtlas domain model. When `jsonschema` is installed, it also validates the
normalized JSON export against the packaged schemas in `chordatlas.schemas`.
Source-tree schemas remain available as a fallback for local development.
Use `chordchart schemas --check` before committing schema changes to catch drift
between canonical package resources and the repo mirror.

If `jsonschema` or the schema files are unavailable, validation continues and
prints a clear `Schema validation skipped` diagnostic to stderr. Provenance
warnings do not make a chart invalid. Malformed input and JSON Schema failures
do.

## Snapshot Workflow

Open-string chart snapshots cover the default rendered chart outputs. Research
comparison snapshots cover a filtered research export from
`examples/research-recording-comparison.yaml`:

- `tests/snapshots/research-recording-comparison.effects-remaster.csv`
- `tests/snapshots/research-recording-comparison.effects-remaster.json`
- `tests/snapshots/research-recording-comparison.effects-remaster.metadata.json`

Regenerate the research comparison snapshots only when the comparison export
contract intentionally changes:

```bash
chordchart snapshots list
chordchart snapshots check research-comparison
chordchart snapshots check research-comparison --format json
chordchart snapshots check research-comparison --diff-dir snapshot-diffs
chordchart snapshots regenerate research-comparison
chordchart snapshots check
```

Available snapshot targets are `open-string` and `research-comparison`. Omitting
the target checks or regenerates all snapshot families. Drift checks print a
compact unified diff for each changed snapshot. Large inline diffs are capped;
use `--diff-dir` to write complete `.diff` artifacts for review.

`chordchart snapshots check --format json` emits machine-readable status,
target, checked files, drift files, per-file truncation status, line counts, and
optional diff artifact paths. Exit codes are unchanged.

See [docs/ci.md](docs/ci.md) for GitHub Actions examples that parse snapshot
JSON, emit annotations, and upload diff artifacts.

## Release Check

Run the full pre-release validation stack from the repository root:

```bash
chordchart release-check
```

The release check is non-interactive and writes diagnostics to stderr. It runs:

- Schema mirror check.
- Snapshot verification for all registered snapshot families.
- Python compilation over `src/` and `tests/`.
- Full `pytest`.
- Temporary wheel and sdist build inspection for packaged schema resources.

Release checklist:

1. Update canonical schemas in `src/chordatlas/schemas/` when the JSON contract changes.
2. Run `chordchart schemas --sync`.
3. Regenerate snapshots when renderer or export output intentionally changes.
4. Run `chordchart release-check`.
5. Investigate any non-zero exit before publishing.

## License

ChordAtlas is licensed under the MIT License. See [LICENSE](LICENSE).

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
- Recording-comparison filters and CSV export.
