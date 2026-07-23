# ChordAtlas User Manual

ChordAtlas currently has two local interfaces:

- ChordAtlas Studio imports an authorized PCM16 WAV, displays its waveform,
  provides synchronized transport controls, and can create a read-only machine
  chord draft.
- The `chordchart` command writes structured guitar charts in YAML and exports
  Markdown, plain text, or JSON. It can also compare claims about different
  recordings, such as studio, live, demo, remaster, or isolated-stem versions.

Studio is a browser interface backed by a loopback-only process on your device.
It does not upload audio. Its automatic output remains visibly unreviewed; the
CLI remains the reviewed chart and export interface.

## 1. What You Need

- Python 3.11 or newer
- A terminal
- A text editor for YAML files
- A local copy of the ChordAtlas repository

To check your Python version:

```bash
python3 --version
```

If your system uses `python` for Python 3, you may use `python` instead of `python3` in
the commands below.

## 2. Install ChordAtlas

Open a terminal and move to the repository directory:

```bash
cd /path/to/ChordAtlas
```

Create an isolated Python environment:

```bash
python3 -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install ChordAtlas and its development tools:

```bash
python -m pip install -e ".[dev]"
```

Confirm that the command is available:

```bash
chordchart --help
```

The environment is active when your shell prompt normally shows `(.venv)`. In a new
terminal session, return to the repository and activate `.venv` again before running
ChordAtlas.

To leave the environment:

```bash
deactivate
```

## 3. Use Local Audio in ChordAtlas Studio

Start Studio from an activated environment. `--project` must name an existing
directory that will own the private media workspace:

```bash
chordatlas-studio --project /absolute/path/to/project
```

Stage 1 Studio currently supports macOS and Linux because its private-storage
checks use POSIX ownership and permission semantics. The Stage 0 CLI can still
be installed and used on Windows; do not claim Windows Studio support until a
platform-specific storage adapter and validation are added.

Studio opens in the default browser. If it does not, copy the printed local URL
from the terminal into a browser. Keep the terminal process running while using
Studio.

The supported format is intentionally narrow: RIFF/WAVE containing mono or
stereo 16-bit integer PCM at 8–192 kHz, no longer than two hours and no larger
than 128 MiB by default. MP3, AAC, video, floating-point WAV, and
hosted-platform pages are not supported.

To import and audition a recording:

1. Choose a local WAV file.
2. Check the authorization acknowledgement only if you own the recording or
   have permission or another right to process it.
3. Select **Import WAV**. Studio validates the complete file before publishing
   a usable asset.
4. Use **Play**, **Pause**, or the Position slider to listen and seek.
5. Drag across the waveform or enter start and end sample frames to set a loop.
6. Select **Clear loop** to return to normal playback.
7. Select **Stop Studio** when finished. Imported media remains in the project.

To acquire an authorized direct HTTPS WAV:

1. Choose **Direct HTTPS WAV**. This adapter accepts a URL whose response is
   the WAV bytes themselves; a webpage that embeds or describes audio is not a
   direct-media URL.
2. Enter a safe display name. Do not copy credentials, tokens, or private URL
   details into this public-facing label.
3. Enter the HTTPS URL. Stage 5 supports only the default HTTPS port and no
   embedded username, password, cookies, account login, custom authorization,
   private-network destination, or hosted-platform extraction.
4. Confirm only if you own the recording or have permission or another right
   to retrieve and process it. ChordAtlas records this acknowledgement before
   performing DNS lookup or making a network request.
5. Select **Acquire WAV**. The URL field is cleared after the request is
   accepted. Inspect the safe phase and byte progress, or select **Cancel**.
6. A failed or cancelled retryable request can be retried as a new immutable
   attempt. Retry downloads from byte zero because remote content can change.
7. On success, the acquired source is selected automatically. Use the same
   waveform, playback, analysis, review, approval, and export controls as a
   local import.
8. On any terminal attempt, select **Forget private locator** when retry is no
   longer needed. This removes the saved URL from the complete linked retry
   family and disables retry while preserving attempt history, an already
   imported MediaAsset, review work, and charts.

ChordAtlas validates the complete bounded response as PCM16 WAV before a source
becomes visible. A timeout, rejected redirect, prohibited destination,
unsupported response, invalid WAV, interruption, or cancellation removes
temporary bytes and leaves existing project state unchanged. Acquisition never
starts chord analysis automatically.

The full URL is private project state because its path or query may contain a
signed secret. ChordAtlas does not place it in Studio status, source labels,
SongChart, Markdown, text, or JSON exports. Retrieval reveals your network
address to the origin, and project-local locator retention is not encryption.
The safe attempt details show the opaque attempt ID, phase, failure code, and
retryability without revealing the locator or destination.
See the [Stage 5 implementation guide](stage5-implementation.md) for the exact
network, retention, and source-authorization boundary.

To create and audition an unreviewed machine draft:

1. Select an imported source.
2. Choose **Entire recording**, or set a valid range loop and choose
   **Current loop**.
3. Select **Analyze chords**. Analysis runs in one bounded local worker and
   never starts merely because playback starts.
4. While queued or running, continue using playback or select
   **Cancel analysis**. Cancellation becomes final only after the worker stops.
5. On success, inspect the tempo, beat, key, chord, confidence, and ranked
   alternate hypotheses under **Machine draft · Unreviewed**.
6. Select a chord proposal to seek to its start. Switching the saved-run
   selector restores earlier immutable attempts.
7. Failed or cancelled work can be retried as a new run without overwriting the
   earlier attempt.

The baseline engine supports major, minor, and `N.C.` hypotheses and limits one
run to ten minutes. Analyze a shorter current loop when needed. It is a
deterministic reference engine, not a claim of production accuracy, and its
numeric confidence is not calibrated.

To correct a successful machine draft without changing it:

1. Select **Start review**, or choose an existing entry under **Saved review**.
2. Keep the raw machine lane visible for comparison. In the editable lane,
   select a chord block to seek to its start.
3. Select **Accept current** when the proposal is correct, choose a ranked
   candidate, or enter a manual chord label and select **Rename**.
4. Use **Mark N.C.** for an intentional no-chord region. Use **Mark Unknown**
   when the harmony is still unresolved; these states are not interchangeable.
5. Split at the current playback position, merge compatible neighbors, move a
   shared boundary, or move an interior segment with the explicit adjacent
   adjustment. Invalid gaps, collisions, and zero-length segments are rejected.
6. Use the range controls to replace an exact interval with `N.C.`. Add,
   rename, move, or remove section markers with their frame controls.
7. Use **Undo** and **Redo** across saved revisions. **Reset to raw** requires
   confirmation and creates another reversible revision.
8. Adjust mistimed boundaries individually, then select **Accept unchanged
   boundaries** to explicitly review any machine boundaries you kept.
9. Review or resolve every segment and boundary, then select **Finish review**.
   This marks the revision `ready_for_approval`; it is still not approval.
10. In **Guitar-aware SongChart**, enter a title and optional artist/key, confirm
    Standard tuning, capo, sounding chord notation, and a complete constant-4/4
    frame grid. Every reviewed section marker must equal a measure boundary.
    Seek during playback and use **Add current playhead as measure boundary** to
    assemble the list, then review the ordered integer frames.
11. Review each chord's inversion, built-in/manual voicing choice, playability,
    and public voicing note. Select **Build exact preview**.
12. Inspect the exact Markdown/text chord-reference rendering or normalized
    JSON and every issue's code, measure/frame/chord details, and consequence.
    SongChart
    1.0.0 keeps chord order but cannot carry exact frame durations, meter, or
    the approved grid; those exact values remain in the private promotion
    result. Acknowledge each material issue only after reviewing its consequence.
13. Select **Approve exact preview** to pin the exact review revision, mapping,
    issue digest, and deterministic result. Studio then shows the existing
    Markdown, text, and JSON exports.
14. If approval was a mistake or is superseded, select a reason and **Revoke
    approval**. Revocation blocks future export retrieval. It does not delete
    review history, mutate the generated chart, or retract files already saved.
15. In **Approved chart practice**, select **Prepare approved practice**. This
    pins the exact approval, private measure map, review revision, source,
    MediaAsset, and integer sample-frame timebase without changing the chart.
16. Choose the full approved chart, a distinct section occurrence, an approved
    measure, or **Custom approved frame range**. Repeated names such as two
    Verse occurrences stay separate.
17. Choose 50–125% playback speed. This is ordinary browser playback-rate
    control and may change pitch; ChordAtlas does not provide pitch-preserving
    time stretching.
18. Optionally choose **One approved bar** count-in and enable repeating. The
    count-in uses the confirmed measure grid and synthesized local clicks. It
    does not use raw detector beats, create negative media frames, or run before
    every loop.
19. Select **Save setup and position**, **Start practice**, **Pause and save**,
    or **Reset practice setup**. Restored sessions are always paused and require
    a fresh user action. Space starts/pauses outside form controls, Escape
    cancels the count-in and pauses, and `P` focuses the target selector.
20. Under **Private practice history**, review the project-wide session, head,
    attempt, saved-action receipt, recovery-artifact, and exact record-byte
    totals. The byte total is not a quota or free-space estimate. Count-limit
    warnings never delete anything automatically.
21. **Reset practice setup** creates another immutable saved attempt; it does
    not delete history. To remove the complete private practice-history domain,
    select **Clear all practice history…**, review the fresh totals, and type
    `CLEAR ALL PRACTICE HISTORY` exactly. There is no per-session deletion,
    age pruning, oldest-record pruning, or attempt compaction.
22. A successful clear pauses practice and keeps authorized audio, analyses,
    reviews, approvals, SongCharts, and exported files. If Studio reports that
    history changed, review the refreshed totals and confirm again. If a
    response is interrupted, use the same visible retry action so its
    idempotency key can resolve the already-started reset safely.

Stage 4 deliberately supports constant 4/4, Standard tuning, sounding chord
symbols, and built-in compatible diagrams only. It blocks non-Standard tuning
because the current renderer cannot suppress incompatible built-in shapes. It
does not claim persistent per-chord preferred voicings or capo-relative chart
notation under SongChart 1.0.0.

Every accepted operation is saved in private project storage. If another tab
changes the same review, Studio rejects the stale edit and reloads the current
saved head instead of overwriting it. The displayed operation count and wall
time are local observations only; wall time includes idle time and is not an
accuracy, quality, effort, productivity, or time-saved measurement.

Refreshing the page restores the active local session. A reported import error
does not replace existing valid assets; correct the issue, choose the file
again, re-check the authorization acknowledgement, and retry. Common fixes are
to export a PCM16 WAV, reduce the file below the configured size/duration, or
wait for another import to finish.

Studio stores authorized media under `<project>/.chordatlas/`, which is private
and Git-ignored. API responses and existing chart exports exclude local paths,
content hashes, locators, and media bytes. Waveform cache files may be
regenerated without deleting original media. See
[Stage 1: Local Audio and Synchronized Playback](stage1-implementation.md) for
the storage and security contracts.

Studio is frame-addressed but browser playback is not sample-accurate. Stage 2
candidate ranges share that integer frame clock but do not promote anything
into `SongChart`. Stage 3 reviewed blocks and sections use the same clock and
also remain outside the chart contract. Stage 4 keeps exact grid timing,
approval, warnings, and revocation in private project storage; public chart
exports contain only allowlisted SongChart fields. Stage 6 stores immutable
private practice sessions and attempt checkpoints separately, deletes the
legacy browser-local loop key, never persists playback handles, and keeps
practice state outside every chart export. Its project-wide clear operation
atomically replaces only the practice namespace and recovers interrupted
cleanup on restart; it never prunes immutable records silently. Browser looping remains
interaction-synchronized rather than sample accurate. See
[Stage 2: Chord-Timeline Inference](stage2-implementation.md), its
[synthetic evaluation](stage2-evaluation.md),
[Stage 3: Interactive Review and Correction](stage3-implementation.md), and the
[Stage 3 engineering evaluation](stage3-evaluation.md), plus
[Stage 4: Guitar-Aware Chart Generation](stage4-implementation.md), and
[Stage 6: Local Practice Foundations](stage6-implementation.md).

## 4. Quick Start

From the repository root, create a workspace for your charts:

```bash
mkdir -p charts output
```

Create a starter chart:

```bash
chordchart new charts/my-song.yaml
```

Open `charts/my-song.yaml` in a text editor. Replace the example metadata, sections,
chords, analysis, and notes with your own work.

Validate the edited chart:

```bash
chordchart validate charts/my-song.yaml
```

Preview it as Markdown in the terminal:

```bash
chordchart render charts/my-song.yaml --format md
```

Save the rendered chart to a file:

```bash
chordchart render charts/my-song.yaml --format md > output/my-song.md
```

That is the standard ChordAtlas workflow:

1. Create or copy a YAML chart.
2. Edit the chart in a text editor.
3. Validate it.
4. Render or compare it.
5. Save the output with shell redirection when needed.

## 5. Write a Basic Chart

The smallest useful chart needs a title and one or more sections. Each item under
`bars` represents one measure. A measure can contain one chord or several chord
symbols.

```yaml
title: My Song
artist: Example Artist
key: G
tuning: Standard
capo: "2"
version: "1.0"
confidence: medium

sections:
  - name: Intro
    bars:
      - [G]
      - [D/F#]
      - [Em7]
      - [Cadd9]
    repeat: x2

  - name: Verse
    bars:
      - [G, D/F#]
      - [Em7, Cadd9]
    notes:
      - Keep the open strings ringing.

analysis:
  roman: [I, V6, vi7, IVadd9]
  nashville: [1, 5/7, 6m7, 4add9]

performance_notes:
  - Use a light attack in the verse.
  - Increase intensity on the final repeat.
```

YAML depends on indentation. Use spaces, keep sibling fields aligned, and do not use
tabs. Quote values when YAML could interpret them as another type. Version numbers and
numeric capo labels are good candidates for quotes.

ChordAtlas rejects duplicate YAML keys instead of silently discarding one. This helps
prevent accidental data loss.

### Timed measures

Use the expanded measure form when a bar needs a timestamp or its own provenance:

```yaml
sections:
  - name: Intro
    bars:
      - chords: [G]
        timestamp: "00:00"
      - chords: [D/F#]
        timestamp: "00:06"
```

### Chord diagrams

ChordAtlas automatically builds a chord reference from the chord symbols used in the
sections. The current built-in shape library includes:

`A`, `Am`, `A7`, `Bm`, `B7`, `C`, `Cadd9`, `Cmaj7`, `D`, `Dm`, `D7`, `D/F#`, `E`,
`Em`, `Em7`, `E7`, `F`, `Fmaj7`, `G`, `G/B`, and `G7`.

Other chord symbols are still accepted and appear in the chart, but their reference
entry says that no built-in guitar shape is available. Custom chart-level chord shapes
are not yet supported.

## 6. Document Sources and Confidence

Provenance records explain how a chart claim is known. They can be attached to the
whole chart and to individual metadata fields, chords, analyses, sections, measures,
performance notes, recording notes, recordings, and version entries.

A chart-level record looks like this:

```yaml
provenance:
  source_type: audio
  source_name: Studio recording
  source_url: https://example.invalid/recording
  timestamp_range: 00:00-00:20
  method: human-ear transcription
  contributor: Example Contributor
  confidence: medium
  verification_status: unverified
  claim_origin: inferred
  notes: The bass note is partly masked by the mix.
```

Supported confidence values are `low`, `medium`, and `high`.

Supported claim origins are:

- `observed`: directly audible or visible
- `computed`: derived by software
- `inferred`: an evidence-based estimate
- `verified`: entered from a trusted reference
- `user_entered`: supplied manually
- `unknown`: not yet documented

Supported verification states are `unverified`, `verified`, `disputed`, and `unknown`.

Important rules:

- An `inferred` provenance record must include `confidence`.
- A provenance record with `verification_status: verified` must include at least one
  evidence reference.
- A timestamp range must contain an ordered start and end, such as `00:13-00:18` or
  `01:02:10-01:02:30`.

Example verified evidence:

```yaml
provenance:
  source_type: stem
  source_name: Isolated guitar stem
  method: human-ear transcription
  confidence: high
  verification_status: verified
  claim_origin: observed
  evidence_refs:
    - ref_id: guitar-stem-verse-1
      source_name: Guitar stem
      timestamp_range: 00:31-00:47
      notes: Open B string is clearly audible.
```

Use `metadata_provenance`, `chord_provenance`, or `analysis_provenance` when the source
belongs to a specific claim:

```yaml
chord_provenance:
  Cadd9:
    source_type: audio
    source_name: Studio recording
    timestamp_range: 00:13-00:18
    method: human-ear transcription
    confidence: medium
    verification_status: unverified
    claim_origin: inferred
```

## 7. Track Revisions

Treat chart revisions like source-code revisions. Update the top-level `version` and
record meaningful changes in `version_history`:

```yaml
version: "1.2"
version_history:
  - version: "1.0"
    changes:
      - Added the initial transcription.
  - version: "1.1"
    changes:
      - Corrected the bridge harmony.
  - version: "1.2"
    changes:
      - Added alternate voicings.
      - Verified the outro against isolated stems.
```

## 8. Render a Chart

The `render` command writes its document to standard output. View it in the terminal or
redirect it to a file.

### Markdown

```bash
chordchart render charts/my-song.yaml --format md > output/my-song.md
```

Markdown is the best general-purpose format for repositories, documentation sites, and
sharing a readable source document.

### Plain text

```bash
chordchart render charts/my-song.yaml --format txt > output/my-song.txt
```

Plain text works well in terminals, email, and simple practice notes.

### JSON

```bash
chordchart render charts/my-song.yaml --format json > output/my-song.json
```

JSON is the canonical machine-readable export. It normalizes the hand-authored YAML,
includes `schema_version`, and always preserves complete provenance data. The current
contract is documented by `schemas/song-chart.schema.json`.

### Provenance detail

Markdown and text rendering support three provenance modes:

- `minimal`: compact default; only uncertainty that affects reading is surfaced
- `standard`: confidence, status, and compact evidence
- `research`: full source, method, contributor, notes, and evidence details

Examples:

```bash
chordchart render charts/my-song.yaml --format md --provenance standard
chordchart render charts/my-song.yaml --format txt --provenance research
```

The provenance option does not reduce JSON data. JSON always contains the complete
normalized record.

## 9. Compare Recordings

Declare every recording source with a stable ID, then scope recording notes to those
IDs.

```yaml
recordings:
  studio:
    title: Original studio recording
    source_url: https://example.invalid/studio
    version_label: 1978 album release
  live:
    title: Live performance
    source_url: https://example.invalid/live
    version_label: 1981 concert

structured_recording_notes:
  Effects:
    category: effects
    notes:
      - text: Light chorus
        recording_id: studio
        claim_origin: inferred
        confidence: medium
        severity: medium
      - text: Dryer amp tone
        recording_id: live
        claim_origin: observed
        confidence: high
        severity: medium
```

`recording_ids` accepts a list when a claim applies to several sources. The singular
`recording_id` is convenient for one source. Every referenced ID must exist in
`recordings`.

Create a comparison report:

```bash
chordchart compare charts/my-song.yaml --format md > output/my-song-comparison.md
```

Available comparison formats are `json`, `md`, `txt`, and `csv`.

```bash
chordchart compare charts/my-song.yaml --format json > output/comparison.json
chordchart compare charts/my-song.yaml --format txt > output/comparison.txt
chordchart compare charts/my-song.yaml --format csv > output/comparison.csv
```

Filter the comparison by category, recording ID, or severity:

```bash
chordchart compare charts/my-song.yaml --format csv \
  --category effects \
  --recording studio \
  --severity medium \
  --source-specific-only > output/studio-effects.csv
```

`--category`, `--recording`, and `--severity` may each be repeated. Category and
severity labels are extensible strings. Recommended categories include `tuning`,
`instrumentation`, `effects`, `arrangement`, `performance`, `mix`, and
`source_quality`.

Comparison exports also support `minimal`, `standard`, and `research` provenance:

```bash
chordchart compare charts/my-song.yaml --format json --provenance research \
  > output/comparison-research.json
```

For a research CSV, preserve the full evidence model in a JSON sidecar:

```bash
chordchart compare charts/my-song.yaml \
  --format csv \
  --provenance research \
  --metadata-json output/comparison.metadata.json \
  > output/comparison.csv
```

`--metadata-json` is valid only with `--format csv --provenance research`. Do not point
the sidecar at the input chart; ChordAtlas refuses to overwrite the source YAML.

## 10. Command Reference

### Create a chart

```text
chordchart new PATH [--force]
```

Creates a complete starter YAML file and any missing parent directories. By default,
ChordAtlas refuses to overwrite an existing path. `--force` replaces an existing file,
so use it only when that data is no longer needed.

### Validate a chart

```text
chordchart validate PATH
```

Loads the YAML, checks domain rules, validates normalized JSON against the installed
schema when the validation dependency is available, and reports provenance warnings.
Provenance warnings are advisory and do not make an otherwise valid chart fail.

### Render a chart

```text
chordchart render PATH [--format md|txt|json]
                       [--provenance minimal|standard|research]
```

The default format is `md`; the default provenance mode is `minimal`.

### Compare recording sources

```text
chordchart compare PATH [--format json|md|txt|csv]
                        [--provenance minimal|standard|research]
                        [--category VALUE]
                        [--recording ID]
                        [--severity VALUE]
                        [--source-specific-only]
                        [--metadata-json PATH]
```

The default comparison format is `json`.

### Show command help

```bash
chordchart --help
chordchart render --help
chordchart compare --help
```

## 11. Validation and Troubleshooting

### `chordchart: command not found`

Activate the virtual environment and retry:

```bash
source .venv/bin/activate
chordchart --help
```

If it is already active, reinstall the project:

```bash
python -m pip install -e ".[dev]"
```

You can also invoke the module directly from an installed environment:

```bash
python -m chordatlas.cli --help
```

### `Invalid YAML`

Check indentation, list markers, colons, and quotation marks. Use spaces instead of
tabs. Also check for a duplicated key; ChordAtlas reports the duplicate and line number
when possible.

### `Invalid chart`

Read the rest of the error message. Common causes include:

- a missing top-level `title`
- a section without `name`
- a measure object without `chords`
- an unsupported confidence, claim-origin, or verification value
- an inferred provenance record without confidence
- a verified provenance record without `evidence_refs`
- a structured recording note that references an undeclared recording ID
- a `schema_version` other than the supported version

### A chord has no diagram

The chord symbol is valid, but the current built-in dictionary does not contain that
shape. The rendered chart keeps the symbol and notes that the diagram is unavailable.

### Validation says schema checking was skipped

Install the development dependency set, which includes `jsonschema`:

```bash
python -m pip install -e ".[dev]"
```

### Output appeared on screen instead of in a file

Rendering and comparison data are written to standard output. Add `>` followed by a
file path:

```bash
chordchart render charts/my-song.yaml --format md > output/my-song.md
```

Shell redirection replaces that output file. Use a new path or preserve the old file
before rerunning the command if needed.

## 12. Examples Included with ChordAtlas

Render the general example:

```bash
chordchart validate examples/open-string-progression.yaml
chordchart render examples/open-string-progression.yaml --format md
```

Explore the evidence-rich comparison example:

```bash
chordchart validate examples/research-recording-comparison.yaml
chordchart compare examples/research-recording-comparison.yaml \
  --format json \
  --provenance research
```

The starter produced by `chordchart new` is also a field guide. Remove sections you do
not need and keep the parts relevant to the chart.

## 13. Maintainer Commands

The following commands are for contributors maintaining ChordAtlas itself. They are not
required for ordinary chart authoring.

Check that the repository's schema mirror matches the packaged canonical schemas:

```bash
chordchart schemas --check
```

Regenerate the mirror after intentionally changing a canonical schema:

```bash
chordchart schemas --sync
```

List and check golden snapshot targets:

```bash
chordchart snapshots list
chordchart snapshots check
```

Run the complete pre-release validation stack:

```bash
chordchart release-check
```

The release check performs schema, snapshot, compilation, test, and package-build
validation. Run it before publishing a coherent change.

## 14. Safe and Responsible Charting

- Record chords, structure, timing, performance guidance, analysis, and evidence.
- Clearly distinguish direct observations from estimates.
- Give uncertain claims an honest confidence level.
- Keep stable source IDs so recording comparisons remain reproducible.
- Update the chart version and history when musical claims change.
- Do not store or reproduce copyrighted lyrics unless you have the necessary rights.
- Preserve the YAML chart as the editable source; treat rendered files as outputs that
  can be regenerated.
