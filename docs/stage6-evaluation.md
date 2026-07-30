# Stage 6 Evaluation and Fixture Policy

## Purpose

Stage 6 evaluation asks whether an approved private chart makes it faster and
safer to configure the intended practice passage than manual frame entry. It
does not measure musicianship, learning, audio quality, detector accuracy, or
whether audio physically sounded.

## Deterministic Fixture Corpus

The practice fixture generates an original lyric-free sine-wave PCM16 WAV and
the complete Stage 1–6 private artifact chain at runtime. The manifest is
`tests/fixtures/practice/fixture-manifest.json`.

Fixtures contain no proprietary recording, copyrighted lyrics, provider
response, credential, locator, live network dependency, or browser cache.

## Engineering Measurements

Required deterministic checks cover:

- content identity and strict serialization of sessions, targets, attempts,
  heads, and receipts;
- exact full, repeated-section, measure, and custom half-open frame ranges;
- first/last-frame, one-frame, empty, out-of-range, gap, and noncontiguous map
  behavior;
- rate-independent source-frame advancement and integer wall-time rounding;
- approved-grid count-in rational and transition to frame-domain playback;
- CAS conflicts, idempotent retry, immutable parents, and paused restart;
- symlink, hardlink, mode, size, nonfinite, duplicate-key, and replacement
  rejection;
- approval revocation, changed review, missing media, and forgotten locator;
- authenticated same-origin APIs and launch-scoped playback-capability rotation;
- exact aggregate practice counts/bytes, strong-token changes, and count-limit
  warnings without a byte quota;
- whole-history authorization, stale-precondition, idempotent retry, external
  lifecycle-lock serialization, namespace isolation, and restart recovery;
- reset crash injection before quarantine, during empty-root recreation, before
  activation, during purge, and after purge;
- absence from SongChart, Markdown, text, JSON, public source mappings, logs,
  fixtures, wheel, and source distribution.

## Musician Study Protocol

Use authorized redistributable material and a within-participant comparison:

1. Give the musician a named section occurrence and an already approved chart.
2. In the baseline task, configure its loop using manual waveform or integer
   frame controls.
3. In the Stage 6 task, choose the approved section/measure target.
4. Record time from loaded review to correct loop, deliberate interactions,
   wrong-section selections, stale-state warnings, and whether playback began
   unexpectedly.
5. Ask separately about setup effort, control, confidence that the intended
   passage is selected, count-in usefulness, pitch acceptability at the chosen
   rate, and usefulness for guitar practice.
6. Record browser, operating system, playback rate, loop duration, and observed
   boundary overshoot descriptively.

Do not call wall time or operation count musical quality, learning progress,
accuracy, effort saved, productivity, or skill. Do not set a release threshold
until the protocol has enough authorized observations to establish a baseline.

## Accepted Engineering Baseline

The focused Stage 1–6 contract run covers local/remote authorized media,
analysis, review, promotion, acquisition, practice, and Studio integration.
The accepted run passed 226 focused Stage 1–6 tests and 847 repository tests.
The Stage 6-only domain and Studio slice contributed 29 passing tests, including
attempt/head/session crash injection, distinct-key create convergence,
directory-sync observation, Linux descriptor-policy coverage, one-shot range
completion, approval rediscovery, and installed-wheel resource checks.

The private-lifecycle and regression-closure passes expand that slice to 52
tests. New deterministic
coverage verifies exact aggregate counts and bytes, token changes, near-limit
warnings, unsafe symlink/hardlink/mode/oversize rejection, strict typed
confirmation, authenticated same-origin reset, stale ETag rejection, same-key
recovery, concurrent lifecycle serialization, namespace isolation, and restart
convergence across reset publication boundaries. They now include a real
first-entry mid-purge failure, byte-exact preservation of newer active work
during later quarantine cleanup, and saved-attempt/saved-action exact-capacity
matrices. These are storage and interaction guarantees, not musician-outcome
evidence.

The baseline uses deterministic adapters and does not execute the public
real-TLS interoperability protocol. It establishes private persistence,
contract reuse, frame mapping, recovery, and interface behavior—not
real-browser loop precision, pitch quality, public-CDN interoperability, or
musician benefit.

The bounded [Stage 6 practice reliability evidence](stage6-pilot-evidence.md)
adds one macOS managed-Chromium solo operator walkthrough. It records
interaction-grade timing, exact final state, keyboard flow, restart behavior,
and the repairs justified by those observations. It is not a musician study,
cross-platform browser baseline, or pitch/audibility result.

The same evidence record now includes a second disposable Chromium run for the
whole-history reset. It covers exact typed confirmation, keyboard and focus
behavior, duplicate-submit suppression, stale-count reconfirmation,
lost-response same-key retry, newer-history preservation, successful playback
state reset, no-autoplay observation, and adversarial restart cleanup. It found
and justified a narrow Studio usage-rendering repair plus a pending-start guard
required by that repair. It does not establish screen-reader behavior,
cross-browser parity, or cross-platform reset recovery.

A third reduced-limit Chromium characterization closes the exact-capacity
parity gap. The final allowed Start remains successful, persistence controls
stay disabled afterward, programmatic and Space activation send no further
save request, active playback can pause locally without saving, and ordinary
Play/Pause remains usable. This is one-browser interaction evidence, not a
cross-platform timing or accessibility claim.

The regression-closure validation baseline is 52 focused Stage 6 tests, 232
selected Stage 1–6 contract tests, and 852 repository tests.

## Release Gate

Stage 6 passes only when:

1. exact private promotion timing—not SongChart timestamps or raw beats—drives
   all approved targets;
2. practice state remains outside SongChart and every existing export;
3. restore is paused and uses a fresh launch-scoped playback capability;
4. speed and count-in never alter source-frame identity;
5. stale approval/review/media state blocks without silent retargeting;
6. no browser storage becomes authoritative and no playback tick is persisted;
7. authorized fixtures and the real-TLS protocol pass license/privacy review;
8. focused and full tests, release-check, installed-wheel Studio/CLI smoke,
   path verification, privacy scans, and `git diff --check` pass;
9. whole-history reset changes only the private practice namespace, and every
   injected crash leaves either the complete old history or a clean visibility
   commit whose later live work remains separate from the recoverable old
   quarantine.
