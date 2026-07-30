# Stage 4: Guitar-Aware Chart Generation

## Outcome

Stage 4 implements the first explicit path from one immutable
`ready_for_approval` review revision to a validated SongChart 1.0.0. It does not
turn the review head into a chart and does not reinterpret raw inference as
approved truth.

The path is:

```text
exact ReviewRevision
  -> explicit mapping configuration
  -> deterministic preview + private timing map + issue report
  -> acknowledgement of every material issue
  -> immutable ApprovalRecord
  -> immutable PromotionResult
  -> existing SongChart validation and JSON/Markdown/text renderers
```

The implementation lives in `src/chordatlas/promotion/`. Review, analysis,
media, and the chart core do not import it.

## Selected Product Forks

SongChart 1.0.0 cannot represent an exact chord rhythm, meter, frame grid,
capo-relative notation mode, or a selected per-chord shape. The first release
therefore makes four narrow choices:

1. **Rhythm:** allow a basic chart projection only after declaring and
   acknowledging timing loss. The private result retains exact frame spans.
2. **Meter:** constant 4/4 only, with user-confirmed integer-frame measure
   boundaries covering the complete analyzed range.
3. **Notation:** exported chord symbols are sounding harmony. Capo is a
   performance setup field and never silently transposes labels.
4. **Voicing:** use the existing Standard-tuning built-in references, plus
   explicit public notes when no built-in voicing is selected. This is not a
   persistent preferred-voicing model.

These are versioned by `songchart-explicit-grid-v1`. A later mapping policy must
use a new identifier; it cannot repurpose this behavior.

## Why Beats Are Not Bars

Stage 2 supplies beat hypotheses but no reviewed downbeats, meter, beat unit, or
bar phase. Stage 3 reviews chord and section timing, not a beat grid. Stage 4
therefore never derives measures automatically from candidate beats.

The user supplies an ordered, unique integer-frame boundary list. It must:

- begin at the reviewed analyzed-range start;
- end at the reviewed analyzed-range end;
- contain at least one positive-length measure;
- place every reviewed section marker exactly on a measure start;
- contain no duplicate, Boolean, negative, or out-of-order values.

Candidate beats remain useful evidence. A confirmed boundary that is not a beat
hypothesis, or a measure whose beat count does not match the confirmed 4/4
meter, produces a material issue.

## Mapping Semantics

All intervals are half-open `[start_frame, end_frame)`.

- Each reviewed segment is intersected with each approved measure.
- Every non-empty intersection emits its exported symbol in frame order.
- A segment crossing a bar appears in both measures.
- A segment beginning exactly at a measure boundary belongs to the next measure.
- Adjacent equal labels remain separate; promotion does not infer collapse or
  repeats.
- `no_chord` maps to `N.C.`. Unknown or unresolved content cannot be promoted.
- Baseline detector spellings normalize visibly: `C:maj -> C` and
  `A:min -> Am`, including a preserved slash bass.
- Measure timestamps are rounded display strings derived by integer arithmetic.
  They are not the authoritative timebase.
- The private `MeasureProjection` retains every measure range and exact chord
  intersection. It is excluded from the public SongChart.

Section markers start a section at their exact matching measure boundary. A
user-supplied leading section name covers measures before the first marker.
Promotion never invents repeats.

## Guitar Review

Every exported chord except `N.C.` requires one explicit `GuitarDecision`:

- inversion reviewed;
- built-in or manual-required voicing choice;
- playable or needs-adjustment status;
- a public note when a manual voicing is required.

Slash chords require an affirmative inversion/bass review. Missing built-in
shapes and playability adjustments are material issues.

Non-Standard tuning is blocked in this mapping version. The existing renderer
looks up diagrams by chord name and cannot yet suppress Standard-tuning shapes
for an alternate tuning. Blocking avoids presenting a misleading diagram while
preserving SongChart 1.0.0.

## Deterministic Identities

The mapping configuration is canonical JSON and content addressed. The
promotion spec includes:

- exact review session, revision, and reviewed-timeline identities;
- complete normalized mapping configuration;
- mapping version.

The persisted PromotionSpec pins the exact revision, reviewed timeline, and
content-addressed mapping configuration. The preview result identity covers the
spec, normalized SongChart, ordered issues, and private timing map. It excludes
clocks, idempotency keys, and approval identity. Identical exact inputs
therefore produce the same result before and after restart.

Approval adds the stable receipt timestamp and local actor. It pins:

- exact review revision and reviewed timeline;
- mapping configuration, spec, and result;
- issue digest;
- sorted material-issue acknowledgements.

Approval requires a strong Stage 3 head token while holding the review-session
lock. The request must echo the displayed spec ID, result ID, and issue digest;
any mismatch requires a new preview. Later undo or editing moves the review head
but cannot retarget the approval. A private receipt identity distinguishes each
activation and prevents an old revocation token from acting on a later
reapproval even when the wall clock is unchanged. Event timestamps clamp
backward wall-clock movement to preserve nondecreasing audit order.

## Private Persistence and Retry

Private records live under:

```text
<project>/.chordatlas/promotion/
  configs/sha256/
  specs/sha256/
  results/sha256/
  approvals/
  revocations/
  events/<review-session>/
  idempotency/
  locks/
```

Directories are owner-only and immutable record leaves are mode `0600`.
Records are bounded, canonical JSON, regular owner files with one link, and
read without following symlinks. Publication is exclusive and retry verifies
the existing content.

The project-wide idempotency receipt is published before downstream artifacts.
It preserves the first timestamp so a retry converges on the same approval or
revocation identity. A conflicting action under the same key fails.

Approval visibility is an append-only session event. Revocation appends a
terminal event and immutable record. It:

- blocks future retrieval through the authorized export service;
- does not delete the ApprovalRecord or PromotionResult;
- does not mutate review history;
- does not claim to retract files already saved elsewhere.

Re-promotion after revocation creates a new approval over the same deterministic
result when the revision and mapping are unchanged.

## Public Allowlist

SongChart is constructed field by field from safe user choices and reviewed
symbols:

- title, optional artist/key;
- explicit Standard tuning, capo, and chart version;
- projected sections and measures;
- public voicing notes.

`SongChart.analysis` remains empty. Public chart exports exclude local paths,
media bytes, authorization assertions, source/media/run/review/approval IDs,
content digests, model identity and parameters, candidate alternatives,
numeric confidence, raw lineage, edits, telemetry, and private timing maps.

The PromotionResult retains the exact timing map privately. Local Studio
approval status may display private workflow identifiers, but the existing
JSON, Markdown, and text chart exports do not.

## Studio Workflow

After **Finish review**:

1. enter safe chart metadata;
2. enter full-coverage integer-frame measure boundaries;
3. confirm constant 4/4, Standard tuning, and sounding notation;
4. review every inversion, voicing, and playability decision;
5. build the exact preview;
6. inspect the Markdown/text chord-reference rendering or normalized JSON plus
   each issue's code and frame/measure/chord details;
7. acknowledge every material issue;
8. approve the exact preview;
9. inspect JSON, Markdown, or text output;
10. revoke if necessary, with an explicit reason.

All controls are native labeled elements. Issue acknowledgement is not
color-only, dynamic text uses `textContent`, and approval/revocation status is
announced through a live status region.
The current playhead can be inserted as a measure boundary; the final ordered
frame list remains an explicit musician-confirmed grid.

## Complete Scenario Trace

The authorized synthetic Studio test traces the full boundary:

1. generate an original redistributable PCM16 WAV progression in memory;
2. acknowledge authorization and import it into private project storage;
3. decode it into an immutable MediaAsset and shared sample-frame timebase;
4. create a successful local AnalysisRun with beat, key, and chord hypotheses;
5. create a ReviewSession, explicitly accept every proposed label and boundary,
   and finish one immutable `ready_for_approval` ReviewRevision;
6. supply safe chart metadata, a full-coverage integer-frame measure grid,
   Standard tuning, sounding notation, and per-chord guitar decisions;
7. build the deterministic preview and acknowledge its material loss records;
8. approve the exact revision and result;
9. validate normalized SongChart 1.0.0 and retrieve Markdown, text, and JSON;
10. scan those exports for private paths and transcription identifiers;
11. revoke approval, confirm future export retrieval fails, and confirm the
    immutable result and earlier export contents are not retracted.

`tests/test_studio_stage4.py` owns this scenario. The lower-level golden and
adversarial mapping cases live in `tests/test_promotion_stage4.py`.

## Failure Contract

The service fails before approval visibility when:

- the review is not ready or the strong head token is stale;
- the selected revision is not the current exact ready revision;
- the grid is malformed, incomplete, or conflicts with a section marker;
- title, meter, notation, tuning, capo, or guitar decisions are invalid;
- an inversion or manual voicing remains unreviewed;
- a material issue acknowledgement is missing or belongs to another preview;
- normalized SongChart schema validation or existing rendering fails;
- private storage fails an ownership, mode, link, size, or integrity check.

A failed preview writes nothing. A failed approval cannot mutate MediaAsset,
AnalysisRun, candidate timeline, ReviewSession, ReviewEdit, ReviewRevision, or
an earlier SongChart.

## Current Limits

- The implementation does not save exports to arbitrary filesystem
  destinations; Studio retrieves deterministic contents for the user.
- Only constant 4/4 and Standard tuning are supported.
- SongChart still cannot preserve exact chord rhythm or selected per-chord
  voicings.
- The built-in dictionary is small and exact-name based.
- Browser playback remains synchronized for interaction, not sample-accurate
  audio editing.
- Windows Studio support remains unclaimed until its storage adapter passes the
  same private-file checks.

These limits are visible product boundaries, not hidden assumptions.
