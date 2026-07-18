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
- `inferred`: best estimate by a human or automated process.
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
- `SongChart.recordings`
- `RecordingSource.provenance`
- `RecordingNoteGroup.provenance`
- `RecordingNote.provenance`
- `RecordingNoteGroup.category`
- `RecordingNote.category`
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

When a chart declares recording sources and scoped recording notes, renderers add
a derived `Recording Source Comparison` section. That section groups claims by
recording-note category first, then recording source, and lists claims that
differ across studio, stem, live, demo, remaster, or other declared sources.
Recommended categories include `tuning`, `instrumentation`, `effects`,
`arrangement`, `performance`, `mix`, and `source_quality`; custom strings remain
valid for project-specific needs. Optional severity labels remain free-form,
with `low`, `medium`, and `high` as recommended values. Comparison output
includes a compact summary of shared claims, source-specific claims, per-source
claim counts, and severity counts before detailed differences.

The dedicated `chordchart compare` command exposes the same derived recording
comparison as a focused report. JSON output is machine-readable analytics with
category totals, shared/source-specific claim counts, per-recording counts,
severity counts, and detailed source-specific claims. Markdown and plain-text
comparison output reuse the same derived data for review-friendly reports. CSV
output flattens category, recording, group, claim text, severity, and
source-specific status for spreadsheet and database workflows.

Comparison filters are applied in the analytics service, not in individual
renderers. Supported filters include category, recording ID, severity, and
source-specific-only. Source-specific status is computed against the full chart
recording set before recording filters are applied, so a single-source export
can still answer whether a claim differs from other available sources.

Comparison exports use provenance modes parallel to chart rendering:

- `minimal`: compact default; no provenance fields are added to claims or CSV.
- `standard`: add confidence, claim origin, provenance summaries, recording
  source URLs, and recording version labels.
- `research`: add full provenance records and evidence references for JSON, and
  evidence reference IDs for extended CSV.

CSV research exports can optionally write a JSON sidecar with
`--metadata-json`. The sidecar is not a replacement for CSV; it is a companion
artifact for consumers that need full evidence objects while keeping tabular
rows stable. It records schema versions, filters applied, CSV columns, recording
source metadata, full provenance records, evidence references, and the research
comparison payload.

`examples/research-recording-comparison.yaml` is the canonical research fixture
for this workflow. It intentionally includes multiple recording sources,
verified evidence references, inferred claims, confidence levels, severity
labels, recording provenance, and note-level provenance. Keep
`examples/open-string-progression.yaml` simple; use the research fixture for
sidecar, evidence, and source-specific comparison tests.

JSON chart export always preserves complete provenance data.

## Serialization Contract

Hand-authored YAML is an input format optimized for readability and compatibility
with simple charts. The canonical machine-readable contract is the normalized
JSON export produced by `chordchart render --format json`.

The canonical Layer 0 schemas live in `src/chordatlas/schemas/`:

- `song-chart.schema.json` defines the current `SongChart` export shape,
  including version history, sections, simple array measures, and
  provenance-aware measure objects.
- `provenance-record.schema.json` defines the reusable evidence contract used by
  chart-level provenance, provenance maps, section provenance, measure
  provenance, and version-history provenance.
- `recording-comparison.schema.json` defines the derived
  `chordchart compare --format json` analytics contract.
- `recording-comparison-metadata.schema.json` defines the CSV companion sidecar
  produced by `chordchart compare --format csv --provenance research
  --metadata-json`.

Every normalized JSON export includes `schema_version`. The current value is
`1.0.0`.

Recording comparison analytics use a separate `comparison_schema_version`, also
currently `1.0.0`, because the payload is derived from a chart rather than a
preservation representation of the chart itself.

Recording comparison metadata sidecars use `metadata_schema_version`, currently
`1.0.0`.

Runtime schema validation reads packaged resources in `chordatlas.schemas`
first. The repo-root `schemas/` directory is a generated mirror retained for
documentation, review, and source-tree fallback.

Schema mirror workflow:

1. Edit canonical schemas in `src/chordatlas/schemas/`.
2. Run `chordchart schemas --sync` to rewrite the repo-root mirror.
3. Run `chordchart schemas --check` to confirm there is no drift.
4. Run `pytest`.

Packaging tests assert that wheel artifacts contain `chordatlas/schemas/*.json`,
source distributions contain both repo-root and packaged schema files, the mirror
matches the canonical package resources, and installed-package validation works
outside the repository checkout. Source distributions also retain the CI workflow
consumed by the packaged workflow regression test, so `release-check` can run from
an extracted sdist.

Compatibility rules:

- Patch changes may clarify descriptions, add tests, or tighten implementation
  behavior without changing the serialized JSON shape.
- Minor changes may add optional fields that old consumers can ignore.
- Major changes are required for removing fields, renaming fields, changing
  field types, changing enum values, or altering required-field semantics.
- Top-level export keys are strict. Unknown keys are rejected by the schema.
- Provenance invariants are enforced both in Python and in JSON Schema:
  inferred claims require confidence, and verified claims require at least one
  evidence reference.
- Markdown and plain-text rendering are presentation contracts. Golden snapshots
  protect their current default output.
- JSON export is the preservation contract. Golden snapshots protect its exact
  normalized field order and payload shape.
- Recording comparison JSON is an analytics contract. It preserves category and
  severity labels as extensible strings while keeping top-level keys strict.
- CSV comparison output is a tabular export contract with stable columns:
  `category`, `category_label`, `recording_id`, `recording_title`, `group`,
  `text`, `severity`, and `source_specific`.
- Extended CSV is opt-in through comparison provenance modes and appends:
  `confidence`, `claim_origin`, `provenance_summary`, `evidence_refs`,
  `recording_source_url`, and `recording_version_label`.
- CSV metadata sidecars are opt-in and only valid for research CSV exports. They
  preserve full provenance and evidence objects without changing CSV rows.

Snapshot policy:

- `open-string-progression.*` snapshots protect default chart rendering.
- `research-recording-comparison.effects-remaster.*` snapshots protect filtered
  research comparison CSV, comparison JSON, and metadata sidecar JSON.
- Regenerate only the snapshot family affected by an intentional contract
  change, then run the full test suite.
- Use `chordchart snapshots list`, `chordchart snapshots check [target]`, and
  `chordchart snapshots regenerate [target]` for deterministic snapshot
  maintenance. Omitting the target means all registered snapshot families.
- Snapshot checks print compact unified diffs for drifted files. Inline diffs
  are capped to keep CI logs readable; `--diff-dir` writes complete `.diff`
  artifacts when deeper review is needed.
- `chordchart snapshots check --format json` is the CI integration contract for
  snapshot status. It reports target, checked files, drift files, truncation
  status, diff line counts, and optional diff artifact paths while preserving
  the text-mode exit codes.
- See `docs/ci.md` for GitHub Actions examples that parse snapshot JSON, emit
  build annotations, and upload full diff artifacts.

Schema changes are contract changes and must be covered by tests. If a schema
change intentionally breaks compatibility, document the migration path before
changing `schema_version`.

## CLI Validation

`chordchart validate` performs validation in layers:

1. Load the YAML input into the domain model.
2. Validate the normalized JSON export against the contract schemas when
   `jsonschema` and the schema files are available.
3. Report provenance warnings from the loaded chart.

Malformed input and JSON Schema failures are fatal and return a non-zero exit
code. Provenance warnings are advisory because incomplete evidence is allowed,
but the warning must be visible. If schema validation cannot run because the
optional dependency or schema files are unavailable, the command returns success
for otherwise valid charts and prints a `Schema validation skipped` diagnostic.
Installed packages should validate from packaged schema resources first, without
requiring a `schemas/` directory in the current working directory.

## Release Hygiene

`chordchart release-check` is the single pre-release gate for source checkouts.
It is intentionally non-interactive and reports every failure to stderr. The
command runs the schema mirror check, direct snapshot verification, Python
compilation, full `pytest`, and temporary wheel/sdist build inspection. Build
artifacts are created in a temporary directory, and Python bytecode caches under
`src/` and `tests/` are removed after the check.

The release command does not rewrite generated files. If schema mirrors or
snapshots drift, update them with the documented sync/regeneration workflow and
rerun the gate.

## Design Constraint

Do not bury uncertainty. ChordAtlas should make strong charts possible, but
honest charts mandatory.
