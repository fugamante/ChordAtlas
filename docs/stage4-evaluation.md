# Stage 4 Musician-Study Protocol

## Purpose

This protocol evaluates whether Stage 4 helps an authorized local-audio review
become a useful, playable chart. It separates musical quality from observed
operations and elapsed time. Event counts and wall time are not substitutes for
accuracy, effort, confidence, satisfaction, or musicianship.

The repository fixture proves deterministic plumbing only. It is not evidence
that automatic transcription is accurate on released music or that ChordAtlas
is faster than manual transcription.

## Corpus and Authorization

Use only:

- musician-owned recordings with written study consent;
- commissioned or public-domain performances with documented permission; or
- redistributable research material whose license explicitly permits the use.

Do not commit proprietary recordings, hosted-platform downloads, copyrighted
lyrics, private locators, or participant identifiers. Keep the corpus manifest
separate from public chart output and record its authorization basis.

For the first study, include constant-4/4 songs only and state that exclusion.
Stratify the corpus across:

- clean versus dense mixes;
- acoustic versus electric guitar;
- basic triads/sevenths versus inversions;
- no capo versus capo performance;
- clear versus ambiguous section boundaries;
- passages with intentional `N.C.`.

Alternate tunings are excluded because Stage 4 blocks them.

## Reference Annotation

Two qualified musicians independently annotate:

- exact frame chord intervals;
- sounding chord symbols and slash bass;
- `N.C.` intervals;
- measure boundaries and section starts;
- capo and tuning;
- playable Standard-tuning chord-reference outcome.

Predeclare equivalence rules for enharmonic spelling, omitted fifths, extensions,
inversions, `N.C.`, section naming, pickups, and ambiguous harmony. Resolve
disagreements through a blinded third-musician adjudication. Preserve both
initial annotations and the adjudicated reference.

## Study Design

Use a counterbalanced within-participant design:

- condition A: ChordAtlas import, inference, review, mapping, and approval;
- condition B: manual chart creation from the same authorized audio using a
  defined neutral editor.

Counterbalance song and condition order to reduce learning effects. Do not let
participants see the adjudicated reference. Use a fresh project for each trial,
fixed software versions, the same audio interface, and the same declared
playback controls.

Before timing begins, train participants on:

- machine proposals as hypotheses;
- exact-frame review versus lossy SongChart projection;
- sounding notation and capo semantics;
- material issue acknowledgement;
- revocation's non-retraction behavior.

## Measurements

### Musical outcome

- duration-weighted sounding chord-symbol agreement;
- root-only and major/minor agreement;
- inversion/slash-bass agreement;
- `N.C.` precision, recall, and duration agreement;
- measure-boundary and section-start agreement;
- chord order per measure;
- playable built-in-shape correctness;
- capo, tuning, and diagram-policy errors;
- blinded second-musician usefulness and playability rating.

### Correction and mapping operations

- primary candidate acceptances;
- alternate selections;
- manual label, `N.C.`, split, merge, move, resize, and section edits;
- undo/redo/reset operations;
- measure-boundary entries and revisions;
- guitar-decision changes;
- material warnings acknowledged;
- preview rebuilds, validation failures, abandonment, and revocation.

Report operations descriptively. A lower count may mean a better draft, an
overlooked error, or a coarse workflow; it is not automatically less effort.

### Time

Report separately:

- wall time from import to valid approval;
- active interaction time under a predeclared idle threshold;
- review time before `ready_for_approval`;
- mapping/approval time after readiness;
- manual-condition time to the same validation endpoint.

Wall time includes listening, thought, interruptions, and idle unless otherwise
declared. Do not label either measure “time saved” without a controlled baseline
and uncertainty interval.

### Safety and comprehension

- whether participants can explain that raw inference remains private;
- whether they distinguish review readiness from approval;
- whether they understand sounding versus capo-relative notation;
- whether they identify declared timing loss;
- whether they understand revocation cannot retract existing files;
- privacy incidents, authorization mistakes, or attempted unsupported tuning.

### Participant-reported experience

Collect separate post-task ratings and short explanations for:

- perceived correction effort;
- confidence in harmonic and bar mapping;
- satisfaction with the playable chart;
- perceived control over automatic suggestions;
- task success and willingness to use the result for practice.

These self-reports remain distinct from operation counts, active time, musical
agreement, and adjudicator ratings.

## Analysis

Report per-song and per-participant values before aggregates. Include medians,
interquartile ranges, paired differences, and uncertainty intervals. Show
abandonment and validation failures rather than excluding them.

Do not set superiority or effort thresholds until baseline evidence exists.
Pre-register any later release threshold with:

- corpus version;
- participant inclusion criteria;
- primary endpoint;
- chord-equivalence policy;
- missing-data handling;
- minimum practically important difference.

## Stage 4 Engineering Baseline

The synthetic CC0 fixture at
`tests/fixtures/promotion/fixture-manifest.json` verifies:

- exact frame-grid mapping;
- detector-style major/minor normalization;
- section projection;
- SongChart 1.0.0 validation;
- deterministic preview/result identity;
- approval pinning, retry, head movement, export, and revocation;
- private-field exclusion.

It contains labels and frame boundaries only—no audio recording or lyrics.
Passing it supports a reproducible engineering claim, not a musician-value
claim.
