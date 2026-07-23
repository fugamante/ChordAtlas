# Stage 6 Practice Reliability Evidence

## Evidence Class and Claim Boundary

This record is a `solo_operator_transport` engineering walkthrough. It is not a
musician study, accessibility certification, or evidence of learning,
musicianship, usefulness, pitch quality, physical audibility, time saved, or
sample-accurate playback.

The walkthrough used only the repository-generated lyric-free PCM16 WAV. Its
authorization manifest is
`tests/fixtures/practice/fixture-manifest.json`, SHA-256
`52d590e0593683a9b29a765238cb7d18f75c3a831a4e88fc7794cc1ff4964dde`.
No private source identifier, local path, launch token, cookie, playback
capability, URL, media byte, screenshot, trace, HAR, or participant identity is
retained here.

## Automation Decision

Candidates used the project-driver score:

```text
priority = value + risk reduction + unblocking + evidence + reversibility - cost
```

| Candidate | Value | Risk | Unblock | Evidence | Cost | Reversible | Priority | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Ephemeral Playwright CLI + managed Chromium | 3 | 3 | 3 | 3 | 1 | 3 | 14 | Selected |
| Deterministic adapter only | 2 | 1 | 1 | 3 | 0 | 3 | 10 | Retained, but not real-browser evidence |
| Repository script with an undeclared external browser runtime | 3 | 3 | 2 | 3 | 2 | 3 | 12 | Deferred; not self-contained and not justified by one-platform demand |
| Manual browser control only | 2 | 1 | 1 | 2 | 1 | 3 | 8 | Useful visual smoke, weaker repetition |
| Persistent Playwright dependency and CI | 3 | 2 | 2 | 1 | 3 | 1 | 6 | Deferred pending stable cross-platform evidence |

The selected combination keeps deterministic domain/API/asset checks in the
authoritative suite and uses the ephemeral browser protocol for bounded release
evidence. A source-only repository script would still create an undeclared
external-runtime contract and duplicate the managed wrapper's version and
cleanup responsibilities. It remains deferred until repeated cross-platform
demand establishes an owner and support boundary. The selected harness lived in
a permission-restricted temporary directory and added no repository package,
lockfile, browser download step, CI job, production hook, or retained browser
artifact.

## Executed Environment

- repository HEAD: `dae907e793f7a63c48b41709a21c2719a27bc8a2`;
- operating system: macOS 26.5.2, Apple silicon;
- browser: Playwright-managed Chromium 151;
- Playwright CLI wrapper: 0.1.17;
- modes: managed headless Chromium for repeated characterization and managed
  headed Chromium for an independent interaction smoke;
- fixture timebase: 8,000 integer PCM sample frames per second;
- screen reader: not tested.

The repository was already intentionally dirty with accepted Stage 0–6 work.
Pre- and post-browser status fingerprints matched before implementation edits.

## Executed Cases

| Case | Evidence | Result |
| --- | --- | --- |
| Approval refresh | Active approval returned after reload, Prepare enabled, no autoplay | Pass |
| Full range | `0–32,000` | Pass |
| Repeated Verse occurrences | occurrence 1 `0–16,000`; occurrence 2 `24,000–32,000`, both with measure spans | Pass; zero scripted wrong-occurrence selections |
| First/last measures | `0–8,000` and `24,000–32,000` | Pass |
| One-frame custom range | `7,999–8,000` persisted and restored paused | Domain/persistence pass; audibility and loop quality not assessable |
| Invalid custom range | equal endpoints rejected; reload preserved prior valid `7,999–8,000` | Pass |
| Paused navigation | with repeat off, five seeks from frame 1 through 31,999 settled exactly in 2–4 ms after the active-playback guard repair | Pass; descriptive local observation |
| Paused navigation with repeat configured | after the repeat setup was saved and playback was paused, a seek to frame 20,000 remained exactly at frame 20,000; active playback still wrapped | Pass |
| Saved resume | frame 10,000 restored paused and Start began at frame 10,000 | Pass |
| Rejected playback | forced browser `play()` rejection remained paused; a later seek to frame 20,000 was not clamped | Pass; test-side rejection only |
| Terminal ended state | after a non-loop ended event, a paused seek to frame 20,000 remained under operator control | Pass |
| Count-in cancel | Escape during beat 1 of 4 left playback paused at frame 8,000 after the remaining nominal count-in window | Pass; no audibility claim |
| Count-in completion | at 100%, a playback event followed one 4-beat approved-grid count-in after about 1.06 s and began near frame 8,000 | Pass; not a musical-usefulness result |
| Keyboard shortcuts | `P` focused the target; Space started outside form controls; Escape paused; Space and `P` inside a number input did not trigger global actions | Pass |
| Tab sequence | target, custom start/end, speed, count-in, repeat, save, start, pause, reset | Pass; no focus trap observed |
| Playback-rate property | 0.50, 0.75, 0.90, 1.00, 1.10, and 1.25 matched the configured browser property; pitch preservation remained disabled | Pass; no pitch-quality claim |

### Boundary Characterization

The initial one-shot probe reproduced a truthful-state defect: the interface
showed frame 12,000 while the paused media clock remained at frame 12,069. The
repair now pauses and seeks the underlying media clock to the exact exclusive
end. Across ten corrected one-second one-shot runs:

- every run paused once without wrapping;
- the final media clock and visible cursor were exactly frame 16,000;
- request-animation-frame observations saw 5–45 frames of pre-clamp overshoot;
- no sample-accuracy claim is made.

Across 20 repeated one-second loops at 100%, request-animation-frame
observations recorded 2–80 overshoot frames, with a nearest-rank p95 of 73
frames (9.125 ms) and a maximum of 10 ms. Three-wrap and two-wrap short-loop
checks also completed at 50%, 100%, and 125%. These numbers characterize one
local browser run; they are not release-wide timing guarantees.

Headed Chromium independently completed a 500 ms one-shot range with four
observed pre-clamp frames and an exact final media clock/cursor, then confirmed
the `P` focus shortcut.

## Evidence-Backed Repairs

1. Non-repeating completion now synchronizes the paused media clock with the
   exact exclusive range end instead of only displaying that end.
2. Practice-boundary enforcement now applies only during active playback, so a
   paused operator can navigate outside the selected practice range.
3. Repeated Prepare actions for an already complete content-addressed session
   no longer consume new idempotency receipts.

Playback-active state is set only after the browser accepts `play()` and is
cleared on rejection, explicit pause, one-shot completion, and terminal
non-loop end. Intentional repeat restart becomes active again only after the
browser accepts the replay.

The browser console produced only a missing-favicon response. It did not affect
practice behavior and was not expanded into an unrelated product change.

## Practice-History Lifecycle Evaluation

Practice history remains bounded by project-global count caps: 1,000 sessions,
20,000 immutable attempts, and 40,000 idempotency receipts. A reduced-limit
reproduction confirmed that exhausting receipts can permanently block new
actions. The redundant-Prepare repair removes one avoidable source of receipt
growth.

Automatic age-based or oldest-record pruning is rejected because it would
silently shorten retry semantics or break parent-linked immutable history.
Attempt compaction is rejected for the same reason. Aggregate usage visibility
and an explicit whole-practice-history reset are now implemented as a separate
private-lifecycle pass. Studio reports exact validated counts and logical bytes,
with count-based warnings at 90% and no aggregate-byte quota. Reset requires
authenticated same-origin access, a fresh strong whole-history ETag, an
idempotency key, and the exact typed phrase `CLEAR ALL PRACTICE HISTORY`.

The lifecycle pass uses an external shared/exclusive lock, an out-of-namespace
durable reset receipt, atomic root quarantine, empty-root recreation, and
descriptor-safe restart cleanup. Its 52 focused Stage 6 tests cover aggregate
identity, unsafe entries, namespace isolation, concurrency, strict request
authority, safe retry, and crash behavior across the transaction. Reset removes
the complete practice-history namespace only; it preserves authorized media,
analysis, review, approval, SongChart, and exports. No automatic threshold
deletion behavior was added.

## Reset Browser and Recovery Evidence

A second bounded macOS run exercised the destructive interaction in
Playwright-managed headless Chromium 151. The Studio server and project lived in
permission-restricted temporary directories. The harness retained no browser
profile, screenshot, trace, HAR, cookie, launch token, local path, or media
copy, and it added no package, lockfile, browser dependency, or CI workload.

| Reset case | Evidence | Result |
| --- | --- | --- |
| Keyboard open | Enter on the clear-history trigger opened the modal and focused the confirmation input | Pass |
| Safe cancellation | Escape and keyboard-activated Cancel closed only a pre-submit modal and returned focus to the trigger | Pass |
| Exact confirmation | Lowercase, leading-space, and trailing-space variants stayed disabled; pasting the exact phrase enabled submit | Pass |
| In-flight dismissal | Escape and Cancel could not close the modal after submission became pending | Pass |
| Duplicate submit | delayed-response injection plus repeated Enter and `requestSubmit()` produced one POST | Pass |
| Live status | pending, busy, stale, unresolved, replay, and success messages were exposed through the existing live-status regions and post-action focus targets | Pass; no named screen-reader run |
| Stale history | a save in a second tab made the first tab's ETag stale; counts refreshed from one attempt/receipt to two, confirmation cleared, and reconfirmation was required | Pass |
| Lost response | the server completed reset while the response was deliberately dropped; the modal became non-dismissible and offered a result check | Pass |
| Idempotent retry | the result-check POST reused the exact first request key and resolved the durable empty result | Pass |
| Newer-history replay | a new session created after the lost-response reset survived the same-key replay and produced an explicit warning | Pass |
| Successful clear | visible sessions, heads, attempts, and action receipts became zero while the active approval remained available | Pass |
| Playback reset | a saved 75% rate, count-in, repeat, and range were cleared to 100%, off, unchecked, and disabled | Pass |
| No autoplay | a page-level play-event counter remained zero throughout successful reset | Pass |
| Recovery usability | cleanup-incomplete and true mid-purge probes exposed an empty active namespace that accepted new private practice work | Pass |
| Replacement and tampering | replacement quarantine and schema-invalid private-field receipt tampering remained blocked by integrity checks; unrelated replacement content was not purged | Pass |
| Restart convergence | interrupted cleanup completed after a fresh service start without exposing mixed old and new history | Pass |

The initial page load exposed one concrete reliability defect:
`renderPracticeUsage` used JavaScript's comma operator around four practice
controls, so the resulting element was not iterable. Usage became unavailable
and clear-history was disabled before any reset interaction could begin. The
repair uses an array literal for that bounded control set. A follow-up browser
probe then demonstrated that the repaired usage refresh could re-enable Start
during a four-beat count-in. Start now has an explicit pending guard that
survives usage refresh and rejects duplicate activation until startup finishes.
A focused exact-block asset regression assertion and clean browser probes
verify both corrections.

### Regression Closure

The two former disposable recovery observations are now deterministic
repository tests. The mid-purge test performs one real descriptor-relative
unlink before injecting failure, proves the quarantine is partially reduced,
then reconstructs the service and verifies cleanup, stable removed aggregates,
same-key replay, and non-practice isolation. The newer-live-work test blocks old
quarantine cleanup, creates and updates practice in the empty active namespace,
captures every live file byte, then verifies a later restart removes only the
old identity-bound quarantine and preserves the complete live snapshot, head,
attempt, state, and replay result.

An exact-capacity domain matrix now covers saved-attempt exhaustion,
saved-action exhaustion, and both together. A reduced-limit Chromium run first
reproduced the defect: the final allowed Start consumed the last saved-action
slot, `finally` re-enabled Start, and a second activation plus paused Space each
sent a guaranteed-to-fail POST. After repair, the final Start produced one POST,
all persistence controls remained disabled, programmatic reactivation sent no
request, playing Space paused locally without saving, paused Space remained
paused and focused the limit reason, and the main Play/Pause controls remained
usable.

The client now retains the server-owned save capability through asynchronous
Start completion and gates both the button and Space shortcut. It distinguishes
saved-attempt and saved-action limits, never calls logical bytes a quota, and
does not add unsaved practice-start semantics.

These remain local macOS engineering observations rather than cross-filesystem,
cross-browser, or Windows evidence.
The storage boundary assumes owner-only, cooperative same-user processes. A
coherently forged same-UID recovery receipt is outside that trust model; this
pass does not claim journal authentication against a hostile same-UID process.

Final local validation passed 52 focused Stage 6 tests, 232 selected Stage 1–6
contract tests, and all 852 repository tests. Schema mirrors and all registered
snapshots were clean, the authoritative release check passed, the packaged
Studio assets and both installed-wheel command entry points loaded from outside
the repository, JavaScript syntax passed, and `git diff --check` reported no
errors.

## Explicitly Not Executed

- recruitment, consent, or any musician participant session;
- baseline-versus-ChordAtlas setup comparison;
- perceived control, guitarist usefulness, pitch acceptability, learning, or
  musical count-in usefulness;
- screen-reader testing with a named assistive technology;
- Safari, Firefox, or Linux real-browser timing;
- Windows Studio testing;
- public real-TLS acquisition or any operator-controlled endpoint;
- proprietary recordings, copyrighted lyrics, or hosted-platform media.

Linux browser timing remains uncharacterized because the repository has no
accepted browser automation dependency or CI image, and the loopback-only
Studio boundary cannot be exercised from a separate container without adding a
new harness/network contract. Existing deterministic Linux descriptor-policy
coverage remains authoritative for private storage, not browser timing.

The real-TLS protocol remains blocked on an explicitly supplied
operator-controlled endpoint and redistributable synthetic media. Browser
automation must not be used to retain its locator, launch token, cookies,
headers, trace, HAR, screenshots, or downloaded bytes.

## Pending Musician Protocol

The musician-study protocol in `stage6-evaluation.md` remains pending. A future
authorized run must keep baseline and ChordAtlas tasks within-participant,
separate operation/time observations from musical judgments, record consent and
media authorization, and leave every unmeasured outcome explicitly
`not_executed`.
