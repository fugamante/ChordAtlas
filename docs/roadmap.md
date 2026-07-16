# ChordAtlas Roadmap

## North Star

Build the definitive open platform for documenting, understanding, preserving,
and learning guitar performances.

ChordAtlas should become the engineering notebook for guitar music: readable by
musicians, precise enough for researchers, structured enough for software, and
honest about evidence and uncertainty.

## Product Goal

The first product goal is to make ChordAtlas the best CLI-first format for
publication-quality, provenance-aware guitar chord charts.

This means the near-term product is not audio ingestion, community hosting, or a
practice app. The near-term product is a durable chart core that can represent a
song clearly, render it beautifully, preserve its evidence, and survive future
expansion without changing its basic contract.

## Current Strategic Bet

ChordAtlas should grow in capability layers, not feature piles. Each layer must
make the previous layer more valuable while keeping the core data model stable.

The project should not move into detection, playback, accounts, or knowledge
graph features until the chart model can carry the information those systems
would produce.

## Active Milestone: Foundation Gate

Objective: prove that a hand-authored chart can be loaded, validated, rendered,
versioned, and inspected as structured data.

The Foundation Gate is complete when ChordAtlas can reliably support:

- A stable `SongChart` domain model with sections, measures, chords, metadata,
  confidence, version history, analysis, performance notes, recording notes, and
  provenance.
- YAML as the primary human-authored input format.
- JSON export as the machine-readable preservation format.
- A JSON Schema that documents and tests the normalized JSON export contract.
- Markdown and plain-text renderers suitable for review and publication.
- Automatic chord reference generation from chords used in the chart.
- Provenance warnings that expose uncertainty without rejecting incomplete work.
- A validation command that loads a chart, rejects malformed input, and reports
  provenance warnings.
- Focused tests for parsing, rendering, chord lookup, provenance, and CLI
  behavior.
- Architecture documentation that explains domain boundaries and extension
  points.

Deliverable: a professional CLI that can produce a clean, versioned chord chart
from a structured source file.

## Layer 0 - Foundation

Objective: build a rock-solid core data model.

Primary capabilities:

- Song metadata.
- Sections and measures.
- Chord symbols.
- Chord shapes.
- Tuning and capo.
- Confidence and provenance.
- YAML loading.
- JSON export.
- Markdown rendering.
- Plain-text rendering.
- CLI entry points.
- Tests and documentation.

Exit criteria:

- Public data structures are documented.
- Simple YAML remains easy to author by hand.
- Rich provenance can be represented without making simple charts noisy.
- Renderers are deterministic.
- JSON export preserves complete chart data.
- Tests cover the core contract.

## Layer 1 - Professional Chord Charts

Objective: make every chart publication quality.

Primary capabilities:

- Automatic chord dictionary.
- ASCII chord diagrams.
- User-defined chord-shape dictionaries.
- Multiple chord-shape variants.
- Section-level notes.
- Measure formatting.
- Roman numerals.
- Nashville numbers.
- Difficulty rating.
- Chord confidence.
- Structured performance notes.
- Recording-source analytics exports.

Deliverable: charts that are clearer, more honest, and more useful than typical
commercial chord pages.

## Layer 2 - Harmonic Intelligence

Objective: explain why the music works.

Primary capabilities:

- Functional harmony.
- Modal interchange.
- Secondary dominants.
- Borrowed chords.
- Cadence detection.
- Key changes.
- Chord substitutions.
- Bass movement analysis.
- Voice-leading analysis.
- Harmonic tension metrics.

Deliverable: every chart can double as a music theory lesson.

## Layer 3 - Guitar Performance Intelligence

Objective: capture how the guitarist actually played.

Primary capabilities:

- Preferred studio voicings.
- Alternate voicings.
- Fingering optimization.
- Chord economy.
- Hand movement analysis.
- Stretch difficulty.
- Open-string usage.
- Barre usage.
- Position maps.
- Capo optimization.

Deliverable: charts optimized for real guitarists, not just abstract harmony.

## Layer 4 - Recording Intelligence

Objective: document the performance as recorded.

Primary capabilities:

- Guitar count.
- Track roles.
- Instrument observations.
- Pickup and amplifier estimates.
- Effects-chain estimates.
- Double-tracking notes.
- Muting and articulation notes.
- Dynamic annotations.

Deliverable: a musician's production notebook that separates observed facts from
informed estimates.

## Layer 5 - Evidence And Provenance

Objective: make every meaningful claim traceable.

Primary capabilities:

- Evidence records.
- Confidence scoring.
- Verification states.
- Source attribution.
- Timestamp citations.
- Contributor history.
- Disputed interpretations.
- Alternate hypotheses.
- Research mode rendering.

Deliverable: a transparent, research-grade transcription system.

## Layer 6 - Audio Intelligence

Objective: assist human transcribers without replacing judgment.

Primary capabilities:

- Beat detection.
- Tempo detection.
- Key estimation.
- Chord estimation.
- Section detection.
- Stem separation.
- Bass extraction.
- Timing alignment.
- Practice loop generation.

Deliverable: a machine-assisted transcription workflow with human review and
traceable evidence.

## Layer 7 - Practice Platform

Objective: help musicians learn from the chart.

Primary capabilities:

- Chord timeline.
- Interactive playback.
- Speed control.
- Practice loops.
- Difficulty adaptation.
- Learning statistics.
- Chord vocabulary tracking.
- Daily practice suggestions.

Deliverable: an intelligent practice companion built on top of verified chart
data.

## Layer 8 - Musical Knowledge Graph

Objective: turn songs into connected musical knowledge.

Primary capabilities:

- Song DNA.
- Harmonic fingerprints.
- Chord similarity.
- Progression search.
- Cadence search.
- Tuning search.
- Voicing search.
- Guitar technique search.
- Recommendation engine.

Deliverable: discovery through harmony, voicing, technique, and evidence instead
of genre alone.

## Layer 9 - Community

Objective: build a living archive.

Primary capabilities:

- User accounts.
- Contributors.
- Reviews.
- Pull requests.
- Discussions.
- Version comparisons.
- Branches.
- Merge workflow.
- Editorial review.
- Citation system.

Deliverable: a collaborative review workflow for guitar transcription.

## Layer 10 - Performance Atlas

Objective: preserve musical history.

Primary capabilities:

- Recording-source comparison review workflows.
- Live performance comparisons.
- Album versions.
- Demo versions.
- Artist evolution.
- Arrangement comparisons.
- Historical timelines.
- Recording lineage.

Deliverable: a historical atlas of guitar performance.

## Layer 11 - Research Platform

Objective: enable musicological research.

Primary capabilities:

- Statistical reports.
- Chord entropy.
- Harmonic complexity.
- Voice-leading metrics.
- Composer fingerprints.
- Evolution over time.
- Genre studies.
- Exportable datasets.
- API.
- Machine-learning integration.

Deliverable: a platform for researchers and educators.

## Feature Filter

Every feature must satisfy at least one of these goals:

| Goal | Question |
| --- | --- |
| Document | Does it preserve what was played? |
| Explain | Does it teach why it works? |
| Verify | Does it show the evidence? |
| Optimize | Does it help a guitarist play it? |
| Preserve | Does it make the chart more durable over time? |

Features that do not satisfy one of these goals should stay out of the core
product until they have a clearer reason to exist.

## Near-Term Direction

After the Foundation Gate, the recommended next direction is Layer 1:
Professional Chord Charts.

The first Layer 1 work should focus on user-defined chord-shape dictionaries,
chord-shape variants, and better chart rendering. These features deepen the
existing CLI product without forcing premature commitments to audio processing,
hosting, or application UI.
