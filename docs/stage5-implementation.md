# Stage 5: Authorized Direct-Media Acquisition

## Outcome

Stage 5 adds one deliberately narrow remote-input path:

```text
explicit authorization
  -> immutable private acquisition request
  -> validated HTTPS destination
  -> bounded private download
  -> complete PCM16 WAV validation
  -> immutable MediaAsset and SourceReference
  -> existing playback, analysis, review, approval, and export flow
```

The first adapter accepts only an HTTPS URL that returns the WAV bytes
directly. A page that embeds, describes, or plays media is not a direct-media
resource. ChordAtlas does not inspect pages, discover media links, reuse browser
credentials, or bypass provider controls.

The implementation lives in `src/chordatlas/acquisition/`. Downstream analysis,
review, promotion, and SongChart code continue to consume only the accepted
Stage 1 `SourceReference` and immutable `MediaAsset`.

## Source Classes

ChordAtlas distinguishes four source classes before any retrieval:

| Class | Stage 5 behavior |
| --- | --- |
| Local file | Continue through the existing local WAV import |
| Unverified direct-media candidate | Eligible only after explicit authorization |
| Hosted-platform page | Reject; no page parsing or media extraction |
| Unsupported or prohibited source | Reject before DNS or network access |

An `https://` URL is only a candidate. Success is established only after a
bounded response and the existing PCM16 WAV decoder both accept the bytes.
Extensions and media types are hints, not proof.

Stage 5 supports HTTPS on the default port, a DNS hostname, a single safe
display label supplied by the user, and no credentials. It rejects HTTP,
non-default ports, IP literals, URL user information, fragments, control
characters, hosted-platform hosts, and ambiguous local names. Query strings
may carry signed access material, so the entire locator is treated as a secret.

## Authorization Boundary

The server validates an exact affirmative authorization assertion and durably
records the request before calling the resolver or transport. Missing or
invalid authorization performs zero DNS lookups and zero network requests.

The acknowledgement records the user's assertion that they own the recording
or otherwise have permission or a right to retrieve and process it. Reachability
is not evidence of permission, and ChordAtlas does not make a legal
determination.

Changing the URL creates a different request and requires a fresh
acknowledgement. Retrying the exact private request creates a new immutable
attempt linked to the earlier one.

## Network Trust Boundary

The production adapter uses a ChordAtlas-owned HTTPS/1.1 transport boundary:

1. parse and classify the URL without network access;
2. resolve the hostname only after authorization is durable;
3. reject the complete answer if any address is not globally routable;
4. connect the socket to one exact validated numeric address;
5. preserve the original hostname for TLS SNI, certificate hostname checking,
   and the HTTP `Host` header;
6. verify that the connected peer is the selected address;
7. handle each redirect manually through the complete policy again;
8. stream a final `200` response into bounded private staging storage.

Loopback, private, link-local, carrier-grade NAT, multicast, unspecified,
reserved, documentation, metadata-service, mapped, scoped, and transition
addresses fail closed. Mixed public and prohibited DNS answers fail as a unit.
Redirects are HTTPS-only, same-origin, bounded to three hops, and never handled
automatically.

The adapter sends no cookies, authorization header, referer, proxy
configuration, or ambient account material. It requests identity encoding and
rejects content encoding, ambiguous framing, duplicate or conflicting length
metadata, truncated responses, unsupported response types, timeouts, and bytes
beyond the configured limit. The complete file is downloaded before the
existing WAV validator becomes authoritative.

## Private Domain Models

Acquisition records remain outside SongChart:

- `RemoteSourceReference` records an opaque source identity, locator digest,
  adapter identity, authorization basis, and safe display label.
- `AcquisitionRequest` records one immutable attempt, its exact source
  reference, creation time, retry lineage, size limit, and adapter policy.
- `AcquisitionState` records append-only lifecycle revisions and public-safe
  diagnostics.
- `AcquisitionOutput` links a successful attempt to the resulting Stage 1
  source and content-addressed media asset.
- `RemoteLocator` retains the exact URL only in private owner-only storage when
  retry requires it.

The public acquisition mapping is an allowlist: opaque request ID, safe display
name, status, phase, bounded byte progress, retryability, timestamps, and the
resulting safe source ID after success. It excludes the URL, hostname, path,
query, redirects, resolved addresses, headers, credentials, private locator,
staging path, raw bytes, and socket diagnostics.

## Durable Asynchronous Lifecycle

One request follows:

```text
queued
  -> running
  -> succeeded | failed | cancel_requested
  -> cancelled | failed
```

The running phase is refined for the user as resolving, connecting,
downloading, validating, building the waveform, and publishing. Byte progress
is determinate only when a trustworthy total exists.

An idempotency receipt binds one key to one normalized request fingerprint.
Repeating the same submission returns the same attempt; reusing the key for
different input conflicts. A retry is a new attempt and re-resolves and
downloads from byte zero because remote content can change.

Cancellation is cooperative during resolution and transfer and is serialized
against publication. Before source visibility wins, cancellation removes
temporary bytes and publishes no source. After the source commit wins, the
attempt is successful; it never reports a false rollback.
The public state marks final publication as non-cancellable, and Studio labels
it **Finalizing** instead of leaving an ineffective Cancel action visible.

On restart, nonterminal work does not silently resume network activity.
Interrupted attempts become retryable failures, validated orphan staging files
are removed, and an explicit retry is required. Existing assets, successful
attempts, analyses, reviews, approvals, and exported charts remain unchanged.

## Storage and Publication

All acquisition state is project-local under:

```text
<project>/.chordatlas/
  acquisitions/
    requests/
    events/
    outputs/
  private/
    acquisitions/
      idempotency/
      <attempt-secret>.json
    locators/
  tmp/
```

Directories are owner-only and record/stage files are owner-readable only.
Secret and immutable leaves must be regular files with one link; symlink,
hardlink, owner, mode, size, and collision violations fail as storage-integrity
errors.

The acquisition layer never constructs a MediaAsset itself. It completes and
syncs private staging, then calls the Stage 1 publication seam. That seam
re-hashes and validates the WAV, generates the waveform, deduplicates identical
bytes by SHA-256, and publishes source visibility last. Different authorized
locators may point to one deduplicated MediaAsset while retaining separate
source assertions.

Project storage remains authoritative. There is no upload, cloud cache, remote
retention service, account, or cross-project queue.

Terminal attempts expose **Forget private locator**. It removes both saved URL
copies across the complete retry lineage, disables retry, and preserves the
safe request/event history plus any
published source, MediaAsset, analysis, review, approval, or chart.

## Studio Workflow

Studio keeps local import unchanged and adds a separate **Direct HTTPS WAV**
form:

1. enter a safe display name that does not contain locator details;
2. enter the direct HTTPS WAV URL;
3. confirm authorization and disclosed private project-local retention;
4. start the acquisition;
5. inspect safe phase/progress text, or cancel;
6. retry a cancelled or retryable failed attempt as a new attempt;
7. remove the private locator when retry is no longer needed;
8. on success, use the same waveform, playback, analysis, review, approval, and
   export controls as a local import.

The browser performs no preflight fetch. The URL field is cleared after an
accepted submission and never enters browser history or client storage.
Acquisition does not automatically start chord analysis.

## Complete Scenario Trace

The Stage 5 end-to-end fixture uses generated, redistributable PCM16 WAV bytes:

1. submit a direct-media candidate with an explicit authorization assertion;
2. durably create the acquisition request before the injected resolver or
   transport is called;
3. stream the synthetic response through bounded private staging;
4. validate and publish the immutable MediaAsset and safe SourceReference;
5. restore the accepted Stage 1 waveform and playback timebase;
6. run the accepted Stage 2 candidate inference;
7. create and finish a Stage 3 review revision;
8. explicitly approve the revision through the Stage 4 mapping boundary;
9. validate and render SongChart 1.0.0 as JSON, Markdown, and text;
10. scan every public result for locator, credential, path, header, address,
    media-byte, and private-artifact sentinels.

No test contacts a live third-party service.

## Explicit Non-Goals

- Hosted-platform pages, scraping, playlists, manifests, or media extraction.
- Provider APIs, OAuth, cookies, accounts, or custom authorization headers.
- DRM, paywall, regional-control, authentication, or terms bypass.
- HTTP, arbitrary ports, proxies, private-network resources, or custom CAs.
- MP3, AAC, video, transcoding, resume/range download, or batch import.
- Cloud upload, retention, collaboration, or background crawling.
- Automatic analysis after acquisition.
- URL or acquisition fields in SongChart or its exports.

## Supported-Platform Boundary

Stage 5 inherits Studio's POSIX owner/mode storage contract and is supported
only where that adapter has been validated. The CLI remains usable on Windows,
but ChordAtlas does not claim Windows Studio or remote-acquisition support
without a Windows-specific private-storage implementation and validation.
