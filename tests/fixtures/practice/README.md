# Practice fixtures

Stage 6 tests generate mono PCM16 WAV bytes and approved private timing records
at runtime. The audio is an original mathematical sine wave with no lyrics,
performance, provider response, or third-party recording.

The fixture chain is:

```text
generated authorized WAV
  -> immutable MediaAsset
  -> deterministic candidate timeline
  -> reviewed revision
  -> active approval and private measure map
  -> private PracticeSession and PracticeAttempt
```

No test contacts a local or public network source. The real-TLS protocol in the
Stage 6 implementation guide is an operator-run interoperability procedure, not
a live fixture or CI dependency.
