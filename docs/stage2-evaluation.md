# Stage 2 Baseline Evaluation

Engine: `chordatlas.baseline` / `template-chroma` version `1.0.0`

Corpus: generated eight-second, 120 BPM C major–G major–A minor–F major
progression at 8 kHz PCM16. The fixture contains mathematical sine waves and
amplitude pulses only. It contains no human performance, lyrics, or third-party
recording.

## Deterministic result

| Metric | Result |
|---|---:|
| Primary duration accuracy | 81.25% |
| Top-3 duration accuracy | 93.75% |
| Accepted-primary duration | 81.25% |
| Unknown duration | 0.0% |
| Mean boundary absolute error | 4,000 frames / 0.500 s |
| Maximum boundary absolute error | 4,000 frames / 0.500 s |
| Unmatched boundaries | 0 |
| Confidence Brier score | 0.226456 |
| Heuristic correction flags | 3 boundary moves, 2 alternate selections, 1 relabel |
| Heuristic correction-flag rate | 45.0/min |

The engine reproduced the same spec and timeline digests across repeated runs.
It proposed tempo near 117.2 BPM, beat locations, A minor and C major among its
leading key hypotheses, and the four expected primary chord labels. Duration
metrics use exact intersections between half-open reference and predicted frame
ranges; they do not sample a representative midpoint.
Boundary timing uses monotonic position alignment; split/merge count accounts
for unmatched boundaries, and a chord relabel does not create a timing error.

## Interpretation

This result proves deterministic analysis, frame-domain timing, hypothesis
serialization, evaluation, and Studio transport integration. It does not prove
usefulness on guitar recordings:

- the corpus is clean synthesized triads, not a dense mix;
- the baseline vocabulary is only major, minor, and `N.C.`;
- the half-second boundary offsets reduce exact primary duration accuracy to
  81.25% and would require correction;
- numeric confidence is explicitly uncalibrated;
- correction flags are a coarse diagnostic heuristic, not a minimum edit
  script, lower bound, or estimate of observed musician time or effort.

Stage 3 should validate actual time-to-reviewed-export and action counts on an
authorized human-reviewed corpus. A NumPy-only adapter experiment should be
compared against this exact evaluation contract before adding a dependency.
