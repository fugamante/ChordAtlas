# ChordAtlas Roadmap

## North Star

ChordAtlas turns authorized audio into an editable, synchronized,
guitar-aware chord chart.

A musician should be able to import a recording, receive a useful timed first
draft, correct every automatic decision while listening, and approve a chart
without writing YAML. The approved chart should explain not only which chords
are present, but how a guitarist can play them and how each claim was produced.

The shorthand "Shazam for chord charts" describes the immediacy of the desired
experience, not a promise of a single infallible answer. Chord transcription
contains genuine ambiguity. ChordAtlas treats detected labels, boundaries,
keys, voicings, capo positions, and tunings as reviewable hypotheses.

## Product Promise

The primary user outcome is:

```text
authorized audio
  -> synchronized chord hypotheses
  -> fast human correction
  -> approved guitar chart
  -> practice, export, and sharing
```

Success is not detector accuracy in isolation. Success is a musician reaching a
correct, useful chart faster than manual transcription while retaining control
over every result.

## Product Principles

1. **Audio first.** The central workflow begins with a recording, not a schema.
2. **Editable by design.** Every inferred label and boundary can be corrected.
3. **Drafts are not facts.** Automatic output stays visibly unreviewed until a
   user approves it.
4. **Guitar aware.** Abstract harmony and guitar realization are separate
   questions; voicing, capo, tuning, and playability follow harmonic review.
5. **Traceable.** Media identity, analysis version, parameters, uncertainty,
   and edits remain reproducible.
6. **Contract separated.** Raw analysis artifacts do not enter the approved
   `SongChart` contract by accident.
7. **Authorized sources only.** Local user-authorized files come first. Future
   URL adapters must honor provider rules, access controls, and user rights.
8. **No lyric reproduction.** Audio analysis does not broaden the existing
   copyrighted-lyrics policy.

## Stage 0 - Foundation (Current)

The current CLI is supporting infrastructure, not the final product.

Stage 0 provides:

- `SongChart` and related domain models.
- YAML authoring and normalized JSON export.
- JSON Schema contract version `1.0.0`.
- Markdown and plain-text renderers.
- Built-in common guitar chord shapes.
- Version history, confidence, and provenance.
- Recording-source comparison analytics.
- Golden snapshots, schema checks, release checks, and tests.

These capabilities remain useful as the reviewed publication boundary. The
interactive transcription domain will promote an approved review revision into
the existing `SongChart`; it will not put raw detector state into that schema.

Stage 0 remains in maintenance mode while the active vertical slice is built.
Correctness, compatibility, and release reliability continue to be maintained,
but unrelated expansion should not displace the active milestone.

## First Interactive Release - Stages 1 Through 4

Stages 1 through 4 form one active vertical-slice milestone. Each stage has an
independent contract and exit criteria, but the first interactive release is
not complete until one authorized local file passes through all four and
reaches an approved export.

### Objective

Prove one complete local-file workflow:

```text
import
  -> decode and identify
  -> analyze
  -> play and review
  -> explicitly approve
  -> promote to SongChart
  -> validate and export
```

The detailed design is in
[audio-transcription.md](audio-transcription.md).

## Stage 1 - Local Audio Ingestion and Synchronized Playback (Implemented)

Deliver:

- Explicit local-file selection.
- User authorization acknowledgement.
- Immutable media identity based on content.
- Decode and normalization diagnostics.
- Duration and basic media metadata.
- Play, pause, seek, and selected-range looping.
- A waveform with a synchronized time cursor.
- Project-scoped storage that does not publish private local paths.

Exit criteria:

- One supported local fixture imports without YAML editing.
- Waveform, playback, and seeking remain synchronized to the project timebase.
- Decode failures are actionable and do not create a misleading partial asset.
- Re-importing changed bytes produces a distinct media identity.
- No source media or private path enters chart export by default.

## Stage 2 - Chord-Timeline Inference (Implemented Reference Baseline)

Deliver:

- Versioned analysis runs tied to immutable media assets.
- Tempo, beat, and key hypotheses.
- Ordered, timed chord candidate segments.
- Primary labels, ranked alternatives, numeric confidence, and `N.C.` support.
- Model, artifact, parameter, and reproducibility metadata.
- A declared chord-label normalization vocabulary.
- Analysis of the complete file or a selected range.

Exit criteria:

- Analysis produces a replayable candidate timeline.
- Exact timing uses a precise integer timebase rather than display strings.
- Rerunning analysis preserves prior runs and identifies what changed.
- Unknown or extended chord labels remain lossless and editable.
- Failure never masquerades as an empty successful transcription.

## Stage 3 - Interactive Review and Correction (Implemented Local Foundation)

Deliver:

- Chord blocks aligned to playback.
- Visible proposed, reviewed, and unresolved states.
- Rename or choose an alternate chord.
- Mark or insert `N.C.`.
- Split and merge segments.
- Move a complete segment while preserving its duration.
- Move segment boundaries.
- Add and move section markers.
- Undo and redo across every correction operation.
- Segment or range looping during correction.
- Durable review revisions that do not mutate the raw analysis run.

Exit criteria:

- A wrong label can be corrected without leaving the timeline.
- An offset segment can be moved without changing its duration; collisions or
  gaps require visible resolution rather than silent overwrite.
- A mistimed boundary can be corrected by direct manipulation.
- Raw candidates remain recoverable after edits.
- Seeking updates the audible position, cursor, and active chord together.
- Edits survive close and reopen.
- The user can tell which decisions remain unreviewed.
- Finishing requires every machine label and boundary to be explicitly
  accepted or corrected and produces `ready_for_approval`, not an approved
  chart.

## Stage 4 - Guitar-Aware Chart Generation

Implemented foundation:

- Content-addressed approval of one exact `ready_for_approval` revision,
  reviewed-timeline digest, mapping configuration, issue digest, and result.
- Explicit, full-coverage integer-frame measure boundaries; Stage 2 beat
  hypotheses remain evidence and never silently define bars.
- A deliberately narrow confirmed 4/4, sounding-symbol, Standard-tuning
  projection with built-in diagram references or declared manual voicing notes.
- Stable material/information issues for timing, notation, meter evidence,
  diagrams, playability, capo semantics, and private provenance.
- Deterministic projection into `SongChart 1.0.0`, schema validation, and the
  existing Markdown, text, and JSON render paths.
- Append-only revocation that blocks future exports without deleting the
  approved result or claiming to retract earlier files.
- Local Studio controls and API routes for preview, acknowledgement, approval,
  export retrieval, and revocation.

Exit criteria:

- The same revision, exact grid, and mapping configuration produce byte-stable
  chart, issue, timing-map, spec, and result identities.
- The result validates against the existing `SongChart` schema version `1.0.0`.
- Existing chart render and export snapshots remain unchanged.
- Numeric detector confidence and alternatives remain in the linked
  transcription artifact rather than being silently discarded or flattened.
- Promotion reports every unresolved mapping warning.
- One complete scenario reaches a valid export without manual YAML authoring.
- Approval remains pinned after review-head movement; revocation does not mutate
  review history or the immutable result.

Current limits:

- SongChart 1.0.0 preserves chord order per measure, not exact chord position,
  duration, meter, or the private frame grid. Approval requires acknowledgement.
- Stage 4 supports explicitly confirmed constant 4/4 only.
- Exports use sounding chord symbols. Capo-relative shape notation is deferred.
- Non-Standard tuning is blocked because the existing renderer cannot suppress
  incompatible built-in shapes.
- A persistent per-chord preferred-voicing contract remains deferred; the
  current boundary uses compatible built-in references plus public notes.

## Reference Interaction Model

Logic Pro's Chord track is a behavioral reference for region-based analysis and
editable chord timelines. Apple documents audio or MIDI region analysis into
the Chord track, editable chord groups, timeline manipulation, and downstream
use by Session Players:

- [Analyze chords in audio or MIDI regions](https://support.apple.com/guide/logicpro/analyze-chords-audio-midi-regions-logic-pro-lgcp4993e80c/mac)
- [Overview of chords in Logic Pro](https://support.apple.com/en-gb/guide/logicpro/lgcp2633963f/mac)
- [Work with chord groups](https://support.apple.com/guide/logicpro/work-with-chord-groups-lgcp895d79af/mac)

ChordAtlas is independently implemented and is not affiliated with or dependent
on Apple or Logic Pro. The reference validates a user interaction pattern, not
an accuracy claim, public detector API, model dependency, guitar-analysis
capability, or URL-ingestion workflow.

## Evaluation Gate

Evaluation uses a fixed, versioned corpus of recordings that the project is
authorized to process. Reference timelines must document ambiguity and the
chord-equivalence policy used for scoring.

### Inference quality

- Duration-weighted chord-symbol accuracy.
- Root-only and major/minor accuracy reported separately from full symbols.
- Top-k candidate recall.
- `N.C.` precision and recall.
- Boundary median error and percentages within declared tolerances.
- Over-segmentation and under-segmentation.
- Beat, downbeat, tempo, and key accuracy.
- Confidence calibration.

### Operator value

- Time to first playable draft.
- Time from import to reviewed export.
- Correction time compared with transcription from scratch.
- Percentage of primary candidates accepted unchanged.
- Alternate-selection rate.
- Label, boundary, split, merge, insertion, and deletion edits per minute.
- Undo rate and unresolved low-confidence duration at approval.
- Completion and abandonment rates.

Numeric release targets will be set only after a baseline study. The first
product gate is evidence that musicians can reach a reviewed basic
triad/seventh timeline faster than manual entry on the declared corpus, with all
automatic decisions editable and traceable.

## Stage 5 - URL Adapters and Asynchronous Processing (Implemented Narrow Baseline)

The accepted baseline adds one explicitly authorized, direct-HTTPS PCM16 WAV
adapter. It reuses the local-file trust boundary and the complete Stage 1–4
vertical slice.

Delivered:

- Strict local-file, direct-media candidate, hosted-page, and prohibited-source
  classification.
- Explicit durable authorization before DNS or network access.
- Manual HTTPS redirects and validated-IP connection pinning with original-host
  TLS verification.
- Bounded private staging, complete PCM16 validation, and manifest-last
  MediaAsset publication.
- Durable acquisition progress, cancellation, idempotency, interruption
  recovery, and linked safe retry.
- Private locator retention with public allowlisted status and diagnostics.
- Reuse of playback, analysis, review, promotion, validation, and export without
  a SongChart change.

Hosted page URLs are not equivalent to downloadable media permission. Adapters
must not bypass authentication, DRM, paywalls, geographic controls, expiring
access controls, or platform restrictions.

Provider APIs, authenticated sources, additional codecs, cross-origin provider
redirect policies, resumable transfer, and hosted-platform adapters remain
deferred until each has a provider-specific rights and security design.

## Stage 6 - Practice, Collaboration, and Advanced Musical Analysis

### Implemented local-practice foundation

The first Stage 6 slice is implemented without expanding the Stage 5 source
boundary.

Delivered practice:

- Immutable project-private PracticeSession and PracticeAttempt records pinned
  to one exact active approval, source, MediaAsset, review revision, and
  integer-frame timebase.
- Full-chart, distinct section-occurrence, measure, and custom-range selection
  from the exact private Stage 4 measure projection.
- Paused restart with a newly minted playback capability and no browser-storage
  authority.
- Interaction-synchronized looping, 50–125% ordinary browser playback-rate
  control with an explicit pitch warning, and an optional one-approved-bar
  locally synthesized count-in.
- CAS/idempotent persistence, visible revocation/staleness/media diagnostics,
  private history, and no SongChart or export change.
- An operator-run authorized real-TLS interoperability protocol with no live CI
  network dependency.

Deferred practice:

- Practice markers, self-rating, scheduling, and progress.
- Chord-change drills derived from reviewed charts.
- Optional accompaniment driven by approved chords.
- Pitch-preserving time stretching, sample-accurate looping, recording,
  performance scoring, and automatic tempo/downbeat-derived count-in.

Deferred collaboration:

- Contributor accounts and review.
- Version comparison and editorial workflows.
- Shared chart libraries and collections.
- Historical recording and arrangement comparison.

Deferred advanced musical analysis:

- Tuning, capo, bass-note, and inversion hypotheses beyond the Stage 4 review
  baseline.
- Preferred and alternate voicings, fingering, chord economy, and difficulty.
- Stem-assisted transcription and multi-instrument attribution.
- Functional harmony and voice-leading reports.
- Harmonic fingerprints, search, similarity, and recommendation.
- Exportable research datasets, APIs, and reproducible model comparison.

## Capability Disposition

| Capability | Decision | Operating rule |
| --- | --- | --- |
| `SongChart`, YAML, and JSON `1.0.0` | Retain | Approved publication contract |
| Validation, renderers, chord dictionary | Retain | Reuse after promotion |
| Provenance primitives | Retain | Reuse at approval boundary |
| Snapshots and release checks | Retain | Preserve compatibility |
| Recording comparison analytics | Freeze | Maintenance and defects only |
| Research provenance expansion | Freeze | No new variants before the Stages 1-4 core loop |
| Publication and chord-shape polish | Freeze | Only work that blocks the Stages 1-4 core loop |
| Local media ingest and playback | Retain | Stage 1 implemented foundation |
| Candidate timeline inference | Retain | Stage 2 reference baseline implemented |
| Correction UI | Retain | Stage 3 local review foundation implemented |
| Guitar-aware promotion | Retain | Stage 4 implemented approval boundary |
| Direct authorized URL adapter | Retain | Stage 5 narrow HTTPS WAV baseline |
| Hosted-platform adapters | Defer | Require provider and rights design |
| Stem separation and multi-guitar attribution | Defer | Later evidence-driven analysis |
| Local approved-chart practice | Retain | Stage 6 private foundation implemented |
| Practice scheduling and scoring | Defer | Require musician evidence and explicit metric semantics |
| Community and knowledge graph | Defer | Stage 6, after core user outcome works |
| Existing shipped capability | Remove none | Do not discard the foundation |

Strategic language that defines CLI publication as the product or deliberately
postpones audio behind unrelated features is removed. No implemented capability
is deleted by this reset.

## Feature Filter

New work must answer these questions in order:

1. Does it shorten or improve the path from authorized audio to reviewed chart?
2. Does it preserve user control over an automatic decision?
3. Does it protect the analysis-to-publication contract boundary?
4. Does it measurably improve timing, harmonic quality, correction effort, or
   guitar playability?
5. Is it authorized, privacy-preserving, and reproducible?

Work that cannot clear this filter remains deferred even if it fits the
long-term platform vision.

## Immediate Next Pass - Stage 6 Practice Validation

Validate the accepted local-practice foundation without expanding the source
or publication boundary:

- Run the authorized musician setup protocol against full, repeated-section,
  measure, and custom-range tasks.
- Measure time and deliberate interactions to the correct range against manual
  frame setup without calling either learning or musical quality.
- Characterize browser/OS loop overshoot, delayed-play behavior after count-in,
  and pitch acceptability across the bounded rates.
- Run the authorized real-TLS interoperability protocol with operator-owned
  generated media and retain only redacted aggregate evidence.
- Harden only validated defects; keep practice state private and preserve the
  exact integer-frame source of truth.
- Preserve the Stage 5 direct-media policy; do not add hosted-provider behavior
  as incidental practice work.

Accounts, collaboration, cloud retention, advanced analysis, and provider
adapters remain separately gated Stage 6 or later work.
