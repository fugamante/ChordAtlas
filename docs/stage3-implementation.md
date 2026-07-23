# Stage 3: Interactive Review and Correction

Status: implemented local review foundation

Stage 3 turns one immutable Stage 2 `ChordCandidateTimeline` into a durable,
editable review revision:

```text
ChordCandidateTimeline (immutable, visibly unreviewed)
  -> ReviewSession
  -> ReviewRevision + one ReviewEdit
  -> ReviewedTimeline
  -> ready_for_approval
```

It does not modify the media asset, analysis request, analysis run, or raw
candidate timeline. It does not approve, promote, or export a `SongChart`.
Stage 4 owns that explicit mapping boundary.

## Domain boundary

The review domain lives in `chordatlas.review` and remains outside the
`SongChart 1.0.0` model and JSON Schema.

- `ReviewSession` binds one private review to one source, successful analysis
  run, and exact candidate-timeline digest.
- `ReviewEdit` is a normalized, content-addressed operation attributed to the
  fixed local-user role. It carries no arbitrary notes, path, or client-created
  before/after state.
- `ReviewRevision` has one parent, one edit, and one resulting timeline digest.
  The root revision has no edit.
- `ReviewedTimeline` is a content-addressed projection with contiguous,
  positive-length segments and section markers.
- `ReviewHead` is a recoverable cursor with a monotonic generation, strong
  compare-and-swap token, explicit redo path, and local event counters.

Materialization always starts from the raw timeline and deterministically
replays the immutable revision chain. Persisted timeline materializations are
verified caches, not independent truth.

## Time and geometry

Every range is half-open `[start_frame, end_frame)` on the Stage 1 integer
sample-frame timebase. The saved review timeline exactly covers the analyzed
range with ordered, contiguous, positive-length segments. A saved operation
cannot leave an implicit gap or collision.

The initial operation set is:

| Operation | Deterministic effect |
|---|---|
| Accept current | Marks the selected non-Unknown proposal as reviewed |
| Choose candidate | Selects one ranked raw candidate and retains raw provenance |
| Rename | Stores a normalized manual label; unsupported labels stay unresolved |
| Mark N.C. | Records an intentional no-chord decision |
| Mark Unknown | Records an unresolved decision distinct from N.C. |
| Split | Divides one segment at a strict interior frame |
| Merge | Combines adjacent segments only when their effective labels match |
| Move boundary | Moves one shared boundary while both neighbors remain positive |
| Move segment | Translates one interior segment without changing duration and explicitly adjusts both adjacent segments |
| Apply N.C. range | Atomically replaces an exact range and preserves full coverage |
| Section edits | Adds, renames, moves, or removes a unique in-range marker |
| Reset to raw | Creates a reversible revision containing the original projection |
| Accept unchanged boundaries | Explicitly reviews all remaining machine boundaries in one visible revision |
| Finish review | Requires every label and boundary to be explicitly reviewed, then marks the revision `ready_for_approval` |

Generic insertion/deletion and silent snapping are intentionally absent.
Incompatible merges, edge-segment moves, neighbor-consuming moves, duplicate
section frames, gaps, and collisions fail with actionable diagnostics and do
not create a revision.

## Undo, redo, and concurrency

Each accepted edit appends one immutable revision and advances the session head.
Undo moves the head to its parent while retaining the old child as the explicit
redo target. Redo requires that exact revision identifier. Editing after undo
starts a new branch and clears the active redo path without deleting the old
revision.

Every mutation requires:

- an authenticated same-origin Studio session;
- one bounded `application/json` request;
- one strong `If-Match` head token;
- one durable request-bound `Idempotency-Key`.

The head token contains a monotonic generation, so returning to an earlier
revision cannot recreate an old token (the ABA case). A stale tab receives a
precondition failure and must reload.

## Private persistence and recovery

Review state is project-local ignored data:

```text
<project>/.chordatlas/review/
  create-keys/           project-wide durable idempotency receipts
  sessions/<review-id>/
    session.json
    create-idempotency.json
    visible.json         published last; incomplete sessions stay hidden
    events/               authoritative immutable head events
    head.json             repairable current-head cache
  edits/sha256/           immutable normalized operations
  revisions/sha256/       immutable parent links
  timelines/sha256/       immutable verified materializations
  locks/                  cross-process advisory locks
  tmp/                    private atomic-write staging
```

Directories require mode `0700`; files require `0600`. Immutable records reject
symbolic links, non-regular files, foreign ownership, unexpected permissions,
hard links, and oversized JSON. Commits publish content first, then the
authoritative head event, then the replaceable head cache. If a process stops
after the event but before the cache update, the next read repairs the cache
from the event log.

Public Studio payloads omit filesystem paths, media bytes, content digests,
private locators, idempotency material, analysis parameters, and internal model
artifacts. Review records are not included in existing chart exports.

## Studio interaction

After a successful Stage 2 run, Studio can start or reopen a saved review. The
read-only machine lane remains above the editable review lane and continues to
identify its values as raw proposals.

The review lane supports keyboard-operable native controls for all initial
operations. Playback, seek, waveform cursor, raw proposal highlight, and review
highlight use the same frame clock. Mutations pause playback, save atomically,
announce status through a live region, and reload on a concurrent-head
conflict. Reset requires confirmation because it is intentionally lossy in the
current projection, although undo retains recovery.

`Finish review` becomes available only when no segment is unreviewed or
unresolved and no machine boundary remains unreviewed. The UI exposes the
boundary count and a separate **Accept unchanged boundaries** operation rather
than silently attesting to timing. Finish records `ready_for_approval`; it is
not approval. Further edits are blocked until the user selects **Undo** to
resume the prior review revision.

## Local measurements

The session head records accepted edit, undo, redo, and reset event counts.
Studio also derives current applied-edit depth and wall-clock time between
session creation and the current head. Wall time includes idle time. No data is
sent externally.

These values are operational observations only. They are not chord accuracy,
musical quality, productivity, human effort, or time saved. An evaluation
claim requires a declared reference chart, equivalence policy, authorized
corpus, and observed reviewer protocol.

## Acceptance boundary

Stage 3 is accepted when:

- raw Stage 2 bytes remain unchanged before and after review;
- every edit replays deterministically and preserves exact frame geometry;
- undo, redo, branching, restart recovery, stale-head rejection, and safe retry
  pass;
- Studio can restore a saved session and synchronizes both lanes to playback;
- private fields and local paths are absent from public payloads and existing
  exports;
- the authorized fixture manifest documents redistribution rights;
- `SongChart 1.0.0`, its schema, snapshots, and exports remain unchanged.
