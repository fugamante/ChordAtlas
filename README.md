# ChordAtlas

ChordAtlas turns authorized audio into an editable, synchronized, guitar-aware
chord chart.

The target experience is direct: import a local recording, receive a timed
first-draft chord timeline, correct every label and boundary while listening,
then approve and export a playable chart. Automatic results are hypotheses, not
hidden truth; confidence, alternatives, and human edits remain visible.

It intentionally avoids storing or reproducing copyrighted lyrics. Charts
should capture chord names, section structure, timing and performance notes,
analysis metadata, recording observations, and the evidence behind each claim.

## Stage 0 Foundation - Retained

The repository retains the Stage 0 publication foundation. It accepts
hand-authored YAML through a CLI and provides:

- Stable `SongChart` and `ChordShape` data models.
- First-class provenance records for documenting how chart claims are known.
- Built-in guitar chord-shape dictionary for common open-position and basic
  barre chords.
- Automatic chord reference generation for only the chords used in a chart.
- Structured version history with Markdown and plain-text rendering.
- Structured recording notes for grouped parts, effects, tuning observations,
  and multiple sources.
- Markdown, plain-text, JSON, and recording-comparison analytics export.
- JSON Schema contracts for normalized charts and comparison exports.
- `chordchart` CLI with `new`, `render`, `compare`, `validate`, `schemas`,
  `snapshots`, and `release-check` commands.
- Example YAML charts, golden snapshots, and focused tests.

These contracts become the approval and export boundary for the active
audio-transcription workflow. Raw detector output, model metadata, candidate
alternatives, and review edits will remain in a separate transcription domain.

## Active Direction - Audio to Editable Chart

Stages 1 through 6 now supply local PCM16 WAV ingestion, synchronized
frame-addressed playback, a deterministic machine chord draft, and durable
human review revisions, explicit deterministic chart approval, and one narrow
authorized direct-HTTPS WAV acquisition path plus private approved-chart
practice:

1. Import a local audio file, or explicitly authorize a direct HTTPS WAV
   resource, that the user is authorized to process.
2. Decode, fingerprint, and play it against a synchronized waveform.
3. Produce beat, key, and timed chord hypotheses with confidence and
   alternatives.
4. Let the user accept, rename, choose alternatives, split, merge, resize, move,
   mark `N.C.` or Unknown, add sections, and undo or redo.
5. Explicitly review every label and boundary, then finish the revision without
   treating that as chart approval.
6. Explicitly confirm a 4/4 frame grid and guitar setup, preview every mapping
   loss, then approve one exact revision into `SongChart`.
7. Reuse the current validation, chord reference, and export paths.
8. Practice an approved full chart, section occurrence, measure, or custom
   range with a paused-restorable private setup, bounded browser speed, and an
   optional approved-grid count-in.

See the [audio-transcription architecture](docs/audio-transcription.md) for the
domain boundaries, interaction specification, end-to-end scenario, source
authorization policy, evaluation strategy, and milestone acceptance criteria.
The [roadmap](docs/roadmap.md) defines this vertical slice as the active product
milestone.

The [Stage 1 implementation guide](docs/stage1-implementation.md) documents the
supported format, private storage boundary, timebase, local-service security
model, cache lifecycle, and deferred work.
The [Stage 2 implementation guide](docs/stage2-implementation.md) documents
analysis identity, the reference adapter, run lifecycle, candidate vocabulary,
evaluation, and read-only Studio boundary. Its
[synthetic baseline report](docs/stage2-evaluation.md) is plumbing evidence,
not a real-song accuracy claim.
The [Stage 3 implementation guide](docs/stage3-implementation.md) defines the
immutable review history, exact edit semantics, private persistence, Studio
interaction, and approval boundary. Its
[review evaluation](docs/stage3-evaluation.md) is a deterministic operations
baseline, not a musician-effort or musical-quality claim.
The [Stage 4 implementation guide](docs/stage4-implementation.md) defines the
exact-revision approval, explicit measure grid, guitar review, loss report,
revocation, and export boundary. Its
[musician-study protocol](docs/stage4-evaluation.md) separates musical quality
from observed operations and elapsed time.
The [Stage 5 implementation guide](docs/stage5-implementation.md) defines
direct-media classification, authorization-before-network, pinned HTTPS,
private durable acquisition, cancellation, retry, and the unchanged Stage 1
handoff. Its [evaluation policy](docs/stage5-evaluation.md) defines the
synthetic network, durability, privacy, and end-to-end evidence gate.
The [Stage 6 implementation guide](docs/stage6-implementation.md) defines the
private approval-bound practice records, exact target mapping, persistence,
speed/count-in limits, restart behavior, and authorized real-TLS
interoperability protocol. Its
[evaluation policy](docs/stage6-evaluation.md) separates engineering evidence
and practice setup from musicianship or learning claims.

## User Manual

The [ChordAtlas User Manual](docs/user-manual.md) is the canonical guide for
using ChordAtlas Studio and the Stage 0 CLI: installation, authorized local or
direct-HTTPS audio acquisition, playback, approved-chart practice, YAML chart
authoring, provenance, rendering, recording comparisons, and troubleshooting.

## Quick Start

ChordAtlas requires Python 3.11 or newer. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
chordchart --help
```

Create, validate, and render a chart:

```bash
chordchart new charts/my-song.yaml
chordchart validate charts/my-song.yaml
chordchart render charts/my-song.yaml --format md > my-song.md
```

Try the included examples:

```bash
chordchart validate examples/open-string-progression.yaml
chordchart render examples/open-string-progression.yaml --format md
chordchart compare examples/research-recording-comparison.yaml \
  --format json \
  --provenance research
```

Launch the local Studio against an existing project directory:

```bash
chordatlas-studio --project /absolute/path/to/project
```

Studio currently supports macOS and Linux. It accepts authorized mono or
stereo PCM16 WAV files at 8–192 kHz from a local file or the Stage 5
direct-HTTPS WAV adapter, keeps media in private project-local storage, then
provides waveform display, play, pause, seek, and range looping.
Stage 2 can explicitly analyze the entire recording or a selected loop and show
unreviewed tempo, beat, key, chord, confidence, and alternate hypotheses.
Stage 3 can start or restore a private review, edit synchronized chord blocks,
manage sections, undo or redo, reset to raw, and finish at
`ready_for_approval`. Stage 4 can then confirm a full-coverage 4/4 frame grid,
review Standard-tuning guitar choices, preview declared SongChart losses,
approve that exact revision, and retrieve JSON, Markdown, or text exports.
The Stage 6 practice panel can then persist an approved full-chart, section,
measure, or custom range; restore it paused; and apply 50–125% ordinary browser
playback rate with an optional one-approved-bar local count-in. Playback speed
may change pitch, and browser loop timing is interaction-synchronized rather
than sample accurate. See the
[user manual](docs/user-manual.md#3-use-local-audio-in-chordatlas-studio) for
the complete first-run and retry workflow.

See the [user manual](docs/user-manual.md) for Windows activation, YAML field
examples, provenance rules, all export formats, comparison filters, research
CSV sidecars, and troubleshooting.

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

Installed CLI validation reads `chordatlas.schemas` first, so it does not
depend on running from a source checkout.

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
- `chordatlas.provenance` defines evidence, confidence, source, and
  verification primitives.
- `chordatlas.chords` owns built-in chord-shape lookup.
- `chordatlas.render` turns structured charts into Markdown or plain text.
- `chordatlas.compare` derives recording-source comparison analytics, applies
  optional filters, and renders comparison-focused JSON, Markdown, plain text,
  or CSV.
- `chordatlas.io` loads YAML and creates starter examples.
- `chordatlas.cli` exposes the `chordchart` command.

This foundation remains the reviewed publication boundary beneath the active
audio ingestion, inference, and correction workflow.

See [docs/architecture.md](docs/architecture.md) for the detailed architecture
and serialization policy, and
[docs/audio-transcription.md](docs/audio-transcription.md) for the separate
transcription domain that promotes reviewed results into `SongChart`.

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
Source-tree schemas remain available as a fallback for local development. Use
`chordchart schemas --check` before committing schema changes to catch drift
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

Available snapshot targets are `open-string` and `research-comparison`.
Omitting the target checks or regenerates all snapshot families. Drift checks
print a compact unified diff for each changed snapshot. Large inline diffs are
capped; use `--diff-dir` to write complete `.diff` artifacts for review.

`chordchart snapshots check --format json` emits machine-readable status,
target, checked files, drift files, per-file truncation status, line counts,
and optional diff artifact paths. Exit codes are unchanged.

See [docs/ci.md](docs/ci.md) for the GitHub Actions release gate and local/CI
parity guidance.

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

1. Update canonical schemas in `src/chordatlas/schemas/` when the JSON contract
   changes.
2. Run `chordchart schemas --sync`.
3. Regenerate snapshots when renderer or export output intentionally changes.
4. Run `chordchart release-check`.
5. Investigate any non-zero exit before publishing.

The GitHub Actions workflow runs `chordchart schemas --check` followed by
`chordchart release-check`, matching the local release gate. Snapshot
diagnostics remain a separate artifact-producing step when the release gate
fails. See [docs/ci.md](docs/ci.md) for CI/local parity and action pinning
policy.

## License

ChordAtlas is licensed under the MIT License. See [LICENSE](LICENSE).

## Roadmap

ChordAtlas grows in capability layers. The active product goal is the
Audio-to-Editable-Chart milestone: local authorized audio becomes a synchronized,
correctable chord timeline and then an approved `SongChart`.

See [docs/roadmap.md](docs/roadmap.md) for the product sequence and
[docs/audio-transcription.md](docs/audio-transcription.md) for the implementation
boundary.

Near-term work:

- Deterministic promotion into the existing chart contract.
- Guitar-aware tuning, capo, inversion, and voicing review.
- An authorized musician study centered on musical outcomes and correction
  effort.
