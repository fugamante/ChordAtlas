# Stage 3 Review Evaluation

Status: deterministic engineering baseline, not a musician study

Fixture: the project-authored abstract timeline declared in
`tests/fixtures/review/fixture-manifest.json`. It contains three integer-frame
chord segments and ranked alternatives but no recording, melody, lyrics, or
third-party material. The manifest is redistributable under CC0-1.0.

## Deterministic result

| Observation | Result |
|---|---:|
| Accepted review operations | 1 alternate selection |
| Session wall elapsed | 45,000 ms |
| Current applied-edit depth | 1 |
| External telemetry | Disabled |
| Raw candidate mutation | None |
| Saved geometry | Exact contiguous half-open ranges |

The test injects fixed timestamps, creates a review, selects the first
segment's ranked alternate, persists the revision, and re-reads the public
measurements. The resulting edit count and 45-second wall interval are exact
contract checks.

## Adversarial coverage

The Stage 3 suite also exercises:

- endpoint split rejection and compatible merge;
- shared-boundary movement and duration-preserving interior translation;
- edge moves and neighbor-consuming collision rejection;
- atomic N.C. range replacement;
- section add, rename, move, duplicate-frame rejection, and removal;
- explicit Unknown versus intentional N.C.;
- unsupported manual labels remaining unresolved;
- finish blocked until every machine proposal is explicitly reviewed;
- undo, explicit redo, edit-after-undo branching, and raw reset;
- retry idempotency, stale-token/ABA rejection, and concurrent writers;
- crash recovery between authoritative event publication and head-cache update;
- lock symlink rejection without modifying the target;
- Studio same-origin authentication, strong preconditions, safe DOM text
  rendering, privacy, and no promotion/export route;
- byte-identical Stage 2 timeline storage after review.

## Interpretation and limits

This baseline proves that the software counts actual accepted operations,
derives wall-clock duration honestly, survives restart and contention, and
does not mutate raw inference. It does not show that the alternate chord is
musically correct or that 45 seconds is representative:

- the timeline is abstract test data, not a performance;
- the elapsed value is injected and includes hypothetical idle time;
- one operation is not a representative correction workload;
- there is no blind reference comparison or independent musical adjudication;
- no musician completed a task in this test.

The first meaningful product study remains an authorized, human-reviewed local
recording corpus. It should report both musical outcomes and correction
operations, with the reference policy, reviewer protocol, ambiguity handling,
and sample size declared before interpreting effort or quality.
