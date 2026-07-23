# Stage 1: Local Audio and Synchronized Playback

Status: implemented foundation

Stage 1 establishes the private, local-audio boundary for ChordAtlas. It does
not perform chord inference and it does not change `SongChart` or its 1.0.0 JSON
schema. Its contract is deliberately small:

```text
authorized local PCM16 WAV
  -> private SourceReference
  -> immutable content-addressed MediaAsset
  -> deterministic waveform
  -> authenticated local playback
  -> sample-frame cursor and range loop
```

## Delivery surface decision

| Surface | Strength | Constraint | Decision |
|---|---|---|---|
| Browser only | Small UI footprint | Cannot safely reuse the Python domain, decoder, and project store without duplicating them | Rejected |
| Native desktop | Strong file and audio integration | Adds a second application stack before the transcription loop is proven | Deferred |
| Loopback service with packaged web UI | Reuses the Python package, supports real browser audio, and remains replaceable | Requires a narrow local HTTP security boundary | Selected |
| Core library only | Lowest implementation cost | Does not prove audible, synchronized playback | Rejected |

`chordatlas-studio` is a local application, not a remotely deployable service.
It binds to numeric IPv4 loopback only, selects a random port by default, serves
only packaged assets and explicit API routes, and requires a per-launch browser
session. It must not be exposed through a reverse proxy or public bind.
The Stage 1 storage implementation currently targets macOS and Linux; Windows
support requires a separate ownership/ACL implementation and validation.

## Initial media and decoder boundary

Stage 1 accepts RIFF/WAVE files containing mono or stereo, little-endian,
16-bit integer PCM at 8–192 kHz. Files are limited to two hours and 128 MiB by
default. Compressed WAV, floating-point PCM, RF64, AIFF, MP3, AAC, video, and
hosted URLs are rejected with actionable diagnostics.

The decoder boundary is `chordatlas.media.wav`. It validates the RIFF structure,
format metadata, audio length, and full decodability before an asset manifest is
published. A later decoder adapter may normalize additional authorized formats
to a canonical PCM representation without changing the timebase or SongChart.

## Domain and identity

The media domain is separate from `SongChart`:

- `SourceReference` records a user authorization assertion and a safe display
  name. Each import creates a distinct source reference.
- `MediaAsset` records immutable technical metadata and uses the SHA-256 of the
  exact imported bytes as its internal identity. Equal bytes deduplicate; a
  changed recording creates a different asset.
- `LocalLocator` is a private import-channel record. It is never returned by the
  public Studio API.
- `Waveform` is a regenerable derivative, not part of asset identity.

The current media records use a draft, independent version marker. They are not
members of `SongChart.analysis`, and they do not alter `schemas/song-chart.schema.json`.

## Authoritative timebase

One integer PCM sample-frame clock is authoritative:

```text
Timebase(unit="sample_frame", sample_rate, duration_frames)
FrameRange(start_frame, end_frame)  # half-open [start, end)
```

Waveform buckets, playback seek position, loop bounds, and future chord segments
use that same clock. Browser seconds are an adapter detail and are converted at
the edge with `round(currentTime * sample_rate)`. Integer frames are persisted;
floating-point seconds are not. Browser playback is frame-addressed and
event-corrected, not sample-accurate; a future Web Audio adapter is required for
sample-accurate loop scheduling.

## Project storage and lifecycle

Starting Studio for an existing project explicitly creates private storage:

```text
<project>/.chordatlas/
  media/blobs/sha256/       immutable authorized WAV bytes
  media/manifests/sha256/   valid-asset marker, published last
  sources/                  safe source records
  private/locators/         private import-channel records
  cache/waveforms/          deterministic, regenerable derivatives
  tmp/                      private incomplete-import staging
```

Directories use user-only permissions and published files use user read/write
permissions. Import writes to a temporary file, validates and hashes it, then
publishes immutable records without overwriting a collision. The manifest is
written last, so an interrupted import cannot appear valid. A retry can reuse a
verified blob. Orphaned incomplete blobs are inert and may be reclaimed by a
future audited maintenance command.

Waveform cache entries may be pruned and regenerated without deleting the
source reference or media blob. Stage 1 intentionally provides no automatic
media deletion: deletion needs a reference-aware, recoverable policy. To remove
all project media today, the operator must stop Studio and deliberately archive
or remove the project-local `.chordatlas` directory outside the application.

No local path, content digest, private locator, or media byte is present in the
source-list or import JSON returned to the UI. Existing chart exports remain
unchanged and contain no Stage 1 media fields.

## Local security boundary

Studio uses:

- exact `Host` and mutation `Origin` checks;
- rejection of cross-site Fetch Metadata;
- a one-use bootstrap secret in the URL fragment, followed by an HttpOnly,
  SameSite session cookie;
- no CORS support and a restrictive Content Security Policy;
- bounded upload length and concurrency;
- random session-scoped playback handles rather than asset hashes;
- authenticated single-range media reads;
- safe, stable diagnostics without filesystem paths.

These controls reduce drive-by browser access to the loopback process. The
stdlib server remains a deliberately narrow local MVP boundary, not a
production multi-user server.

## First run

From an installed development environment:

```bash
chordatlas-studio --project /absolute/path/to/project
```

Studio opens a local browser page. The first interaction is:

1. Select a supported WAV file.
2. Confirm that you own the recording or are authorized to process it.
3. Import and wait for validation and waveform generation.
4. Select the source, then play, pause, seek, or drag a waveform range to loop.
5. Use the numeric frame controls to address loop boundaries on the shared clock.

An import failure leaves existing valid assets untouched. The same authorized
file can be selected again after correcting the reported issue.

## Fixture authorization

Stage 1 tests generate short sine-wave PCM fixtures in memory. They contain no
recording, performance, lyrics, or third-party media and are not checked in as
audio files. This keeps validation deterministic while preserving the
copyrighted-lyrics and source-authorization policies. The reviewed corpus
manifest and redistribution statement live in
`tests/fixtures/audio/fixture-manifest.json` and
`tests/fixtures/audio/README.md`.

## Acceptance boundary and deferred work

Stage 1 is complete when an authorized local PCM16 WAV can be imported,
validated, stored privately, rendered as a waveform, played, sought, and looped
on the shared frame clock with safe retry behavior.

Explicitly deferred:

- chord, beat, tempo, or key inference;
- chart promotion or edits to `SongChart`;
- URL and hosted-platform adapters;
- accounts, remote upload, cloud retention, and collaboration;
- broad codec support and automatic storage deletion;
- a desktop shell.

The next seam consumes an immutable `MediaAsset` and its `Timebase` to produce
candidate analysis records outside SongChart. Reviewed output alone may later
be promoted through the existing SongChart contracts.
