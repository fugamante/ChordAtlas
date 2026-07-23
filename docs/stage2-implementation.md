# Stage 2: Chord-Timeline Inference

Status: implementation contract

Stage 2 consumes an immutable Stage 1 `MediaAsset` and produces a reproducible,
read-only machine draft. It does not edit or approve a chart:

```text
MediaAsset
  -> deterministic AnalysisSpec
  -> unique AnalysisRun attempt
  -> validated ChordCandidateTimeline
  -> synchronized "Machine draft · Unreviewed" lane
```

No Stage 2 field belongs to `SongChart`, `SongChart.analysis`,
`RecordingSource`, or `MediaAsset`. Stage 3 will own correction, and Stage 4
will own explicit promotion into `SongChart 1.0.0`.

## Adapter decision

The first adapter is `baseline-v1`, a dependency-free deterministic reference
engine implemented with the Python standard library. It emits genuine
tempo/beat, key, major/minor chord, and `N.C.` hypotheses from PCM16 audio. It
exists to prove the analysis contract, offline execution, failure containment,
evaluation harness, and Studio integration. It is not a production-accuracy
claim.

| Adapter | Decision | Reason |
|---|---|---|
| Standard-library bounded DSP | Selected | Smallest offline, deterministic, license-simple contract proof |
| NumPy-only FFT/chroma | Next experiment | Better performance/quality potential with one BSD dependency |
| librosa feature stack | Optional evaluation | Useful beat/chroma primitives, but a much larger dependency tree and no production chord detector |
| Essentia | Rejected for core | Chord detector is experimental and distribution needs an explicit AGPL/commercial licensing decision |
| madmom pretrained chord runtime | Rejected for core | Model licensing, old stable runtime, compiled dependencies, and serialized-model risk |

Primary adapter evidence:

- [NumPy license and installation](https://numpy.org/doc/stable/license)
- [librosa beat tracking](https://librosa.org/doc/0.11.0/generated/librosa.beat.beat_track.html)
- [librosa chroma](https://librosa.org/doc/0.11.0/generated/librosa.feature.chroma_cqt.html)
- [Essentia licensing](https://essentia.upf.edu/licensing_information.html)
- [Essentia chord detection](https://essentia.upf.edu/reference/std_ChordsDetection.html)
- [madmom source and model licensing](https://github.com/CPJKU/madmom/blob/main/LICENSE)

Future adapters must remain behind the same runtime interface. They may not
download a model at runtime, execute a client-supplied path, or change the media
or chart contracts.

## Analysis identity

`AnalysisSpec` is immutable and content-addressed. Its digest is calculated from
canonical UTF-8 JSON containing:

- media content identity;
- the exact Stage 1 `Timebase`;
- a half-open analyzed frame range;
- engine, engine version, model, model version, and vocabulary version;
- strictly typed, allowlisted integer parameters;
- a deterministic seed when relevant.

Timestamps, paths, environment diagnostics, run IDs, and source display names
are excluded. Changing any musical input or reproducibility parameter changes
the spec digest.

Each execution is a separate opaque `AnalysisRun`. Retrying an identical spec
creates a new run attempt and preserves the earlier failed, cancelled, or
successful run.

## Run lifecycle

Run state is distinct from immutable request identity:

```text
queued -> running -> succeeded
                  -> failed
queued -> cancelled
running -> cancel_requested -> cancelled
```

State revisions and transition events are persisted independently. A terminal
state never transitions. `succeeded` is written only after the candidate
timeline and run-output link have been validated and durably published.
Failed, cancelled, and interrupted runs never claim a timeline.

Analysis runs outside HTTP request handling in a bounded local worker process.
Stage 2 permits one active run per Studio project. Cancellation is cooperative
first and terminates the worker after a bounded grace period; it is not reported
as complete until the worker can no longer publish. A process-held advisory
lock prevents concurrent project workers and is released by the operating
system after a crash; the next startup converts abandoned nonterminal attempts
to retryable `analysis_interrupted` failures.

Worker IPC is bounded UTF-8 JSON rather than object deserialization. Collection
sizes are capped, worker error codes map to supervisor-owned public messages,
and only a fully parsed, contract-valid timeline can be published.

## Candidate timeline

`ChordCandidateTimeline` is immutable and content-addressed independently from
the run that produced it. Equal validated output from separate attempts has the
same timeline digest.

It contains:

- the exact media `Timebase`;
- the analyzed half-open frame range;
- tempo, beat, and key hypotheses;
- ordered, non-overlapping chord segments;
- explicit `chord`, `no_chord`, or `unknown` state;
- raw labels and normalized canonical symbols;
- ranked primary and alternate candidates;
- integer confidence in parts per million;
- normalization status and notes;
- engine, model, and vocabulary provenance.

Gaps are represented explicitly as `unknown`. Floating-point seconds and
non-finite numbers are never persisted as timing or confidence truth.

## Private persistence

Stage 2 uses project-local ignored storage:

```text
<project>/.chordatlas/analysis/
  specs/sha256/          immutable deterministic requests
  runs/<run-id>/         request, revisioned state, events, output link
  timelines/sha256/      immutable validated candidate payloads
  claims/                exclusive active-worker claims
  tmp/                   private incomplete worker output
```

Directories require mode `0700` and files `0600` on the currently supported
macOS/Linux storage implementation. Public Studio mappings omit media and
artifact digests, filesystem paths, environment variables, stack traces,
private parameters, credentials, and media bytes.

## Studio interaction

The Studio analysis panel is explicit; playback never starts inference
implicitly.

1. Select an authorized imported source.
2. Choose the entire recording or the current valid loop.
3. Select **Analyze chords**.
4. While queued or running, playback remains available and the panel exposes a
   truthful phase with an indeterminate progress indicator.
5. Cancel requests remain pending until the worker acknowledges them.
6. Success shows tempo, beat, key, confidence, alternatives, and ordered chord
   proposals in a semantic read-only list.
7. Selecting a proposal may seek playback, but cannot edit a label or boundary.
8. Prior runs remain selectable. Retry reuses the selected immutable request
   scope and configuration, records `retry_of`, and creates a new attempt.

The lane always says **Machine draft · Unreviewed**. It contains no rename,
split, merge, drag, approve, promotion, or SongChart export controls.

## Evaluation contract

The deterministic synthetic corpus contains generated clicks, silence, and
major/minor triads. It has no human performance, copyrighted lyrics, or
third-party recording.

The evaluator reports:

- exact half-open-intersection-weighted primary chord accuracy;
- exact half-open-intersection-weighted top-k rescue accuracy;
- mean and maximum absolute frame error across monotonically aligned
  boundaries, plus unmatched-boundary count;
- confidence Brier score;
- accepted-primary duration;
- unknown duration;
- heuristic correction flags per audio minute, decomposed into
  relabel, alternate selection, boundary move, split, merge, insert, delete,
  and `N.C.` actions.

Correction flags per minute are a deterministic offline diagnostic, not a
minimum edit script, lower bound, or estimate of observed musician effort.
Stage 3 must measure actual time and actions required to reach reviewed export.
Synthetic results validate plumbing and reproducibility only; they cannot
establish real-song usefulness. The heuristic aligns boundary positions
independently from chord labels so a relabel is not also flagged as a boundary
move; unmatched positions are represented by split/merge flags.

## Acceptance boundary

Stage 2 is accepted when:

- a generated authorized fixture produces a versioned run and replayable
  candidate timeline without YAML;
- deterministic inputs reproduce the same spec and timeline digests;
- retries preserve prior attempts;
- cancellation, worker failure, invalid output, and interruption remain
  distinguishable and safely retryable;
- every candidate uses the Stage 1 integer sample-frame clock;
- Studio displays an accessible synchronized unreviewed lane;
- evaluation output is deterministic and explicitly synthetic-only;
- SongChart schemas, snapshots, and exports remain unchanged.

Production usefulness remains unproven until an authorized human-reviewed
recording corpus and Stage 3 correction-effort study exist.
