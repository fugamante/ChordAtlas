# Stage 5 Evaluation and Fixture Policy

## Purpose

Stage 5 evaluation asks whether an explicitly authorized direct-media request
can reach the existing local transcription workflow without opening a broader
network, privacy, persistence, or publication boundary. It does not measure
hosted-provider coverage and does not establish that a locator is legally
authorized.

## Deterministic Fixture Corpus

The acquisition corpus contains only generated PCM16 WAV material and
synthetic protocol responses. Its manifest records generator ownership,
redistribution status, expected byte length and digest, response framing, and
the failure or success expected from each case.

Fixtures must contain:

- no proprietary recording;
- no copyrighted lyrics;
- no provider page or captured third-party response;
- no live credential or signed URL;
- no live network dependency;
- no request to a LAN, metadata service, or public internet host.

Network behavior is exercised through injected resolvers/transports or a
test-only local TLS boundary whose loopback allowance cannot be enabled by
production configuration.

## Required Measurements

### Authorization and classification

- resolver and transport call count before a durable authorization record;
- local file, HTTPS candidate, hosted page, and prohibited-source decisions;
- stable safe error code, retryability, and absence of locator reflection.

### Destination and TLS policy

- every prohibited IPv4 and IPv6 class;
- mixed safe/unsafe DNS answers and answer limits;
- pinned numeric connection target with original hostname as SNI and `Host`;
- peer mismatch, certificate failure, and HTTPS downgrade;
- relative redirects, loops, limits, cross-origin destinations, and private
  redirect targets.

### Transfer integrity

- valid and conflicting length framing;
- declared size overflow, bounded reads, and truncation;
- content encoding and unsupported media types;
- timeout, cancellation, resolver/transport/supervisor failure, and safe close;
- complete WAV validation before source visibility.

### Durability

- immutable request and event serialization;
- idempotent same-input submission and changed-input conflict;
- cancellation at every phase;
- interruption recovery without automatic network restart;
- retry as a linked new attempt;
- identical bytes deduplicating to one MediaAsset;
- changed remote bytes producing a different MediaAsset;
- no overwrite of existing project state.

### Privacy

A unique sentinel representing each raw URL, query token, hostname, redirect,
header, private locator, staging path, address, and media fragment is searched
across:

- public acquisition status and Studio responses;
- safe source mappings and waveform data;
- analysis, review, approval, and promotion payloads;
- normalized SongChart JSON, Markdown, and text;
- stdout, stderr, exceptions, and captured logs;
- fixtures, Git-visible files, wheel, and source distribution.

Only the explicitly private bounded acquisition and media locator records may
contain the exact URL. **Forget private locator** removes both copies.

## End-to-End Evidence

The acceptance scenario is:

```text
authorized synthetic HTTPS WAV
  -> acquisition
  -> waveform/playback source
  -> candidate timeline
  -> reviewed revision
  -> approval
  -> SongChart 1.0.0
  -> JSON + Markdown + text
```

Passing this trace proves contract reuse and privacy boundaries. It does not
claim remote-origin reliability, broad codec coverage, hosted-platform
compatibility, or musical-model accuracy.

## Accepted Baseline Results

The deterministic Stage 5 acceptance run covered 69 focused acquisition,
MediaAsset, and Studio tests. The complete Stage 0–5 suite covered 800 tests,
including the authorized synthetic URL-to-reviewed-SongChart trace and the
existing schema, snapshot, persistence, synchronization, promotion, and export
checks.

All acquisition network behavior in this baseline is exercised at injected
resolver, socket, TLS, and response boundaries. No public host, proprietary
recording, or live credential is used. The results therefore establish the
bounded state, privacy, framing, and contract behavior; they do not establish
real-CDN interoperability, certificate-lifecycle coverage, musical accuracy,
or musician correction effort.

## Release Gate

Stage 5 is acceptable only when:

1. no resolver or transport action precedes durable authorization;
2. production connections are pinned to validated global addresses while TLS
   verifies the original hostname;
3. redirects and response framing are manual, bounded, and fail closed;
4. failure, cancellation, interruption, and retry never publish partial state;
5. the Stage 1–4 scenario completes through unchanged SongChart exports;
6. privacy sentinels are absent from all public and packaged artifacts;
7. fixture licensing and authorization metadata pass review;
8. focused and full tests, installed-wheel Studio/CLI smoke,
   `chordchart release-check`, and `git diff --check` pass.

## Interpretation Limits

Synthetic fixtures are necessary for deterministic safety testing but do not
represent internet latency, CDN policy, certificate ecosystems, mutable remote
content, or musician usage. A later authorized pilot should report acquisition
completion, failure classes, time to first waveform, retry frequency, and
abandonment separately from chord accuracy and correction effort.
