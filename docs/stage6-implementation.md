# Stage 6: Local Practice Foundations

## Purpose

Stage 6 adds the smallest private practice workflow over one exact active
approval:

```text
active ApprovalRecord
  -> private PromotionResult.timing_map
  -> immutable PracticeSession
  -> immutable PracticeAttempt revisions
  -> paused Studio restore
  -> explicit count-in and playback gesture
```

Practice does not change the approved review, `SongChart`, JSON Schema,
MediaAsset, candidate timeline, or export. It adds no account, upload, provider,
cloud-retention, collaboration, scoring, accompaniment, or advanced-analysis
boundary.

## Authoritative Timing

The exact private Stage 4 `MeasureProjection` sequence is the only practice
grid. Rounded SongChart timestamps, detector beats, candidate tempo, and section
names alone are not timing authorities.

The promotion service validates this complete private chain before Stage 6 can
create or change practice:

```text
ApprovalRecord
  -> PromotionSpec + MappingConfig + PromotionResult
  -> exact ReviewSession + ReviewRevision + ReviewedTimeline
  -> AnalysisSpec + ChordCandidateTimeline
  -> SourceReference + immutable MediaAsset
```

Every measure must be positive, ordered, contiguous, numbered from one, and
cover the exact reviewed range. Every section key is the pair
`(section_ordinal, section_name)` and must occupy one contiguous run. This keeps
repeated names such as two separate Verse occurrences distinct.

Practice targets are immutable projections:

- full approved chart;
- each approved section occurrence;
- each approved measure;
- a user-entered custom half-open range contained by the approved chart.

All ranges remain integer PCM sample frames. Playback speed never multiplies or
reinterprets a source frame.

## Private Domain

### PracticeSession

One content-addressed immutable session pins:

- source and MediaAsset identities;
- exact approval and promotion-result identities;
- exact review session and revision identities;
- sample rate, duration, and approved analyzed range;
- confirmed meter;
- the ordered full, section, and measure targets.

`created_at` is event metadata and does not alter the content identity.

### PracticeAttempt

Every explicit save creates an immutable attempt revision linked to its parent:

- target or custom range;
- safe position inside that half-open range;
- loop enabled/disabled;
- integer playback rate in thousandths;
- count-in mode and exact approved-measure rational;
- recorded time.

The mutable head contains only generation, current attempt, and a strong token.
Updates require that token plus an idempotency key. A receipt is published
before the attempt and head, so a retry can finish a uniquely provable
interrupted update without inventing a second result.

Initial publication writes the immutable attempt, then the head, and publishes
the session record last as the visibility commit. A retry with either the same
or a different idempotency key converges on the one content-addressed session.
Interrupted pre-visibility records remain unlisted; a complete pre-visibility
head is verified and repaired by a later authorized retry.

Preparing an already complete content-addressed session returns its current
head without creating another idempotency receipt. Concurrent first creation
may still leave more than one valid retry receipt, but ordinary refresh or
repeated Prepare actions do not consume the finite receipt budget. The key is
still length-validated, and any existing receipt must prove the same Prepare
action and fingerprint before this convergence shortcut is allowed.

Stage 6 does not write animation frames, `timeupdate` events, inferred
repetitions, practice scores, or continuous telemetry. Attempts are explicit
saved configurations and safe checkpoints, not evidence of listening,
accuracy, ability, learning, productivity, or time saved.

## Storage and Privacy

Records are stored under:

```text
<project>/.chordatlas/private/practice/
  sessions/
  attempts/sha256/
  heads/
  idempotency/
  locks/
```

Directories require owner-only `0700`; JSON and lock leaves require owner-only
`0600`, regular-file type, and one link. Reads use `O_NOFOLLOW`, validate the
opened descriptor, reject oversized/non-UTF-8/non-object/duplicate-key/nonfinite
JSON, and detect replacement during the read. Immutable publication is
exclusive and directory-synchronized; heads use guarded atomic replacement.

Practice records may contain stable private workflow identities and approved
section labels. They never contain:

- a local path or URL;
- credentials, cookies, headers, or locator data;
- media bytes or waveform payloads;
- a launch-scoped playback handle;
- raw inference artifacts or alternate candidates.

Only authenticated same-origin Studio practice endpoints expose the minimal
private setup needed by the local interface. Existing source, approval, chart,
Markdown, text, JSON, and comparison payloads remain unchanged.

The legacy browser-local loop key is deleted when a source loads. Browser
storage is not a practice authority. On restart, Studio mints a new playback
capability, restores the last saved attempt paused, and requires a new user
gesture.

## Private Usage and Explicit Reset

Studio reports descriptor-validated project-wide counts and logical record
bytes for sessions, current heads, immutable attempts, saved-action receipts,
and reset recovery receipts. Session, attempt, and action-receipt counts retain
their existing limits of 1,000, 20,000, and 40,000. A persistent warning begins
at 90% of a count limit. Bytes are accounting for validated records only: they
are not an aggregate quota, a free-space estimate, or an automatic cleanup
trigger.

Usage is available only from the authenticated local Studio session. Its
payload contains aggregates, limits, remaining counts, warnings, and an opaque
strong token. It contains no record names, workflow identities, labels, paths,
URLs, media, timestamps, receipt hashes, or record contents. The token covers
the sorted validated inventory and changes even when a mutation leaves counts
and total bytes unchanged.

The only deletion operation is project-wide **Clear all practice history**.
ChordAtlas deliberately provides no per-session deletion, oldest-N or
age-based pruning, receipt-only cleanup, or attempt compaction. The operator
must review fresh usage and type `CLEAR ALL PRACTICE HISTORY` exactly. The
authenticated same-origin request also requires the current strong usage ETag
and an idempotency key. A stale token changes no files.

All complete practice create, list, get, update, usage, reset, and recovery
operations observe an owner-only external lifecycle lock under:

```text
<project>/.chordatlas/private/practice-maintenance/
```

Ordinary operations take the lock shared before any in-tree create/session
lock. Usage, reset, and startup recovery take it exclusively. The maintenance
lock and reset receipts live outside the resettable practice root.

Reset is a namespace transaction:

```text
prepared durable receipt
  -> atomic practice-root quarantine rename + parent fsync
  -> durable empty practice tree
  -> activated receipt
  -> descriptor-safe quarantine purge
  -> complete receipt
```

The quarantine rename is the visibility commit. A crash before it leaves the
complete old history. At or after it, the visibility commit installs a clean
empty active namespace; recovery preserves any practice work created there
later and removes only the identity-bound old quarantine. The service never
mixes old and new history or exposes a partially deleted live tree. Cleanup
walks opened owner-only directories without following links, rejects foreign
or multiply linked entries, synchronizes each removed directory, and can be
retried with the same idempotency key. Ambiguous or unsafe storage fails
closed.

The reset removes sessions, heads, attempts, in-domain receipts, and operational
practice locks. It does not modify MediaAsset, acquisition, analysis, review,
promotion, approvals, SongChart, exported files, source authorization, or any
other private namespace. Completed reset receipts remain bounded operational
recovery state so a lost response can be resolved safely; they are reported
separately and are not presented as remaining practice history.

## Studio Interaction

The approved practice panel supports:

1. prepare a session from the current active approval;
2. select full chart, one disambiguated section occurrence, one measure, or a
   custom approved range;
3. select 50%, 75%, 90%, 100%, 110%, or 125% browser playback rate;
4. choose no count-in or one approved bar;
5. enable or disable repeated range looping;
6. save setup/position, start, pause-and-save, or reset.

Start resumes the saved in-range position. Choosing a different target or
Reset explicitly seeks to that range's start. With repeat disabled, playback
pauses at the selected half-open range's exclusive end; with repeat enabled,
the same boundary returns playback to the range start.

At one-shot completion, Studio also synchronizes the underlying paused media
clock to the exact exclusive end so the visible cursor does not conceal
interaction-grade overshoot. Practice boundaries apply during active playback;
while paused, the operator remains free to seek elsewhere in the authorized
media.

Studio treats playback as active only after the browser accepts `play()`.
Rejection, explicit pause, one-shot completion, and terminal non-loop end clear
that state before later seeks. A repeated-loop restart becomes active again
only after the browser accepts replay.

Native controls provide keyboard navigation. Outside form fields, Space toggles
practice start/pause, Escape cancels count-in and pauses, and `P` focuses the
practice target. Status changes use a polite live region and never announce
every cursor update.

Practice Start, Save, Pause-and-save, and Reset each require capacity for one
more immutable attempt and saved-action receipt. The server-owned usage
capability remains authoritative through asynchronous Start completion. When
either count limit is reached, those persistence actions stay disabled and
Space cannot bypass them. If playback is already active, Space pauses locally
without saving; the main Play and Pause controls remain available. Studio names
the exhausted saved-attempt or saved-action resource and does not describe
logical bytes as a quota.

Speed uses `HTMLMediaElement.playbackRate`. ChordAtlas explicitly disables pitch
preservation where the browser exposes that switch and makes no pitch-quality
claim. Browser seeking and loop enforcement are interaction-synchronized, not
sample accurate.

One-bar count-in uses the confirmed Stage 4 meter and the first overlapping
approved measure. Its beat duration is a rational source-frame duration scaled
by the selected integer rate. Studio synthesizes local short clicks in a
monotonic wall-clock domain. Count-in creates no negative media frames, does not
change the source timebase, and occurs only before the explicit start action,
not before every loop.

## Availability and Recovery

- Approval revocation preserves history but blocks changes and removes the
  approved-ready presentation.
- A changed review requires a new approval; ranges never silently rebase.
- Forgetting a Stage 5 remote locator preserves the immutable local MediaAsset
  and practice.
- Missing or invalid media disables practice without modifying its history.
- A stale ETag or reused idempotency key with different content fails visibly.
- A stale whole-history ETag or wrong reset phrase deletes nothing.
- Interrupted whole-history reset is reconciled before Studio serves practice.
- Restore is always paused; audio and count-in never auto-resume.
- Absence of practice records means no practice history and requires no
  migration.

Studio remains supported only where the accepted POSIX storage adapter is
validated. Stage 6 does not claim Windows Studio support.

## Authorized Real-TLS Interoperability Protocol

This is an operator-run protocol, not CI, a live repository test, or permission
to retrieve third-party media.

1. Generate the repository's original synthetic PCM16 WAV fixture locally.
2. Place only those redistributable bytes on an operator-controlled public
   HTTPS origin with public DNS, a normally trusted hostname certificate,
   default port 443, one exact `Content-Length`, identity encoding, and
   `audio/wav` or `application/octet-stream`.
3. Use no authentication, cookie, signed query, provider page, cross-origin
   redirect, CDN credential, or third-party recording.
4. Confirm control of the media and authorization before entering the direct
   URL in Studio. Keep the exact hostname and URL out of screenshots, issue
   text, test logs, fixtures, and repository artifacts.
5. Verify acquisition reaches one immutable MediaAsset, then complete analysis,
   review, approval, practice creation, paused restart, and SongChart export.
6. Confirm exported JSON, Markdown, and text contain neither the URL nor
   practice identities.
7. Select **Forget private locator**, confirm practice remains available from
   the local MediaAsset, then remove the hosted synthetic object.
8. Record only pass/fail, certificate issuer class, browser/OS, safe failure
   code, byte count, and timings. Do not record the locator, address, response
   headers, playback handle, cookies, or media bytes.

The operator should separately verify wrong-hostname, untrusted, expired, and
peer-mismatch failures in an isolated test harness. Production has no
loopback/private-address allowance and no custom-CA configuration; the Stage 5
pinned HTTPS policy remains unchanged.

## Deferred

- pitch-preserving time stretching;
- count-in derived from unreviewed beat/downbeat hypotheses;
- continuous attempt or repetition telemetry;
- self-rating, mastery, scheduling, drills, accompaniment, recording, or
  performance scoring;
- shared sessions, accounts, uploads, cloud retention, and collaboration;
- provider or hosted-platform adapters;
- additional codecs and advanced musical analysis.
