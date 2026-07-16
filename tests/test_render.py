from pathlib import Path

from chordatlas.io import load_song_chart, song_chart_to_json
from chordatlas.models import SongChart
from chordatlas.render import render_markdown, render_text

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"


def _chart() -> SongChart:
    return SongChart.from_mapping(
        {
            "title": "Test Song",
            "artist": "Test Artist",
            "key": "G",
            "confidence": "High",
            "version_history": [
                {"version": "1.0", "changes": ["Initial transcription."]},
                {
                    "version": "1.1",
                    "changes": ["Corrected Verse 2.", "Changed Cmaj7 to Cadd9."],
                },
            ],
            "sections": [
                {"name": "Intro", "bars": [["G"], ["D/F#"]]},
                {"name": "Verse", "bars": [["Em7"], ["Cmaj7"]]},
            ],
        }
    )


def test_markdown_includes_only_used_chords() -> None:
    rendered = render_markdown(_chart())

    assert "# Test Song — Test Artist" in rendered
    assert "### G\n" in rendered
    assert "### D/F#\n" in rendered
    assert "### Em7\n" in rendered
    assert "### Cmaj7\n" in rendered
    assert "### A\n" not in rendered
    assert "## Version History" in rendered
    assert "### v1.1" in rendered
    assert "- Changed Cmaj7 to Cadd9." in rendered


def test_text_renders_sections_and_metadata() -> None:
    rendered = render_text(_chart())

    assert "Song: Test Song — Test Artist" in rendered
    assert "Confidence: High" in rendered
    assert "VERSION HISTORY" in rendered
    assert "v1.0" in rendered
    assert "INTRO" in rendered
    assert "| G |" in rendered


def test_renderers_omit_version_history_when_absent() -> None:
    chart = SongChart.from_mapping({"title": "Legacy Chart"})

    assert "Version History" not in render_markdown(chart)
    assert "VERSION HISTORY" not in render_text(chart)


def test_markdown_renders_structured_recording_notes() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recording_notes": ["Original flat note"],
            "structured_recording_notes": {
                "Guitar 1": ["Left channel", "Acoustic", "Open voicings"],
                "Estimated tuning": "Standard, approximately 15 cents flat",
            },
        }
    )

    rendered = render_markdown(chart)

    assert "## Recording Notes" in rendered
    assert "### General" in rendered
    assert "- Original flat note" in rendered
    assert "### Guitar 1" in rendered
    assert "- Left channel" in rendered
    assert "### Estimated tuning" in rendered
    assert "Standard, approximately 15 cents flat" in rendered


def test_text_renders_structured_recording_notes_with_checkmarks() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Guitar 2": ["Right channel", "Electric", "Octave doubling"],
                "Estimated tuning": "Standard, approximately 15 cents flat",
            },
        }
    )

    rendered = render_text(chart)

    assert "RECORDING NOTES" in rendered
    assert "Guitar 2\n✓ Right channel\n✓ Electric\n✓ Octave doubling" in rendered
    assert "Estimated tuning\nStandard, approximately 15 cents flat" in rendered


def test_recording_note_semantics_are_hidden_in_minimal_rendering() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Effects": [
                    {
                        "text": "Slight tape saturation",
                        "claim_origin": "inferred",
                        "confidence": "low",
                    }
                ],
            },
        }
    )

    rendered = render_text(chart)

    assert "✓ Slight tape saturation" in rendered
    assert "inferred" not in rendered
    assert "Low confidence" not in rendered


def test_recording_note_semantics_render_in_standard_mode() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Effects": [
                    {
                        "text": "Slight tape saturation",
                        "claim_origin": "inferred",
                        "confidence": "low",
                        "provenance": {
                            "source_type": "audio",
                            "method": "human-ear transcription",
                            "confidence": "low",
                            "verification_status": "unverified",
                            "claim_origin": "inferred",
                        },
                    }
                ],
            },
        }
    )

    rendered = render_markdown(chart, provenance_mode="standard")

    assert "- Slight tape saturation (inferred, Low confidence)" in rendered
    assert "- Confidence: Low" in rendered
    assert "- Status: Unverified" in rendered
    assert "- Evidence: human-ear transcription" in rendered


def test_recording_sources_and_scopes_render_in_minimal_mode() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {
                "studio": {
                    "title": "Studio recording",
                    "source_url": "https://example.invalid/studio",
                    "version_label": "Album version",
                },
                "stems": "Isolated stems",
            },
            "structured_recording_notes": {
                "Guitar 1": {
                    "recording_ids": ["studio", "stems"],
                    "notes": [
                        {
                            "text": "Left channel",
                            "recording_id": "studio",
                            "claim_origin": "observed",
                            "confidence": "high",
                        }
                    ],
                },
            },
        }
    )

    rendered = render_text(chart)

    assert "Recordings\n- studio: Studio recording (Album version | https://example.invalid/studio)" in rendered
    assert "- stems: Isolated stems" in rendered
    assert "Guitar 1 [Studio recording, Isolated stems]" in rendered
    assert "✓ Left channel [Studio recording]" in rendered
    assert "observed" not in rendered


def test_recording_scopes_render_with_semantics_in_standard_mode() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Effects": [
                    {
                        "text": "Light chorus",
                        "recording_id": "studio",
                        "claim_origin": "inferred",
                        "confidence": "medium",
                    }
                ],
            },
        }
    )

    rendered = render_markdown(chart, provenance_mode="standard")

    assert "### Recordings" in rendered
    assert "- studio: Studio recording" in rendered
    assert "- Light chorus [Studio recording] (inferred, Medium confidence)" in rendered


def test_recording_source_comparison_groups_claims_and_differences() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {
                "studio": "Studio recording",
                "live": "Live performance",
            },
            "structured_recording_notes": {
                "Guitar 1": {
                    "category": "instrumentation",
                    "recording_ids": ["studio", "live"],
                    "notes": ["Open voicings"],
                },
                "Effects": {
                    "category": "effects",
                    "notes": [
                        {
                            "text": "Light chorus",
                            "severity": "medium",
                            "recording_id": "studio",
                        },
                        {
                            "text": "Dryer signal",
                            "severity": "low",
                            "recording_id": "live",
                        },
                    ],
                },
            },
        }
    )

    rendered = render_markdown(chart)

    assert "## Recording Source Comparison" in rendered
    assert "### Summary" in rendered
    assert "- Instrumentation: 1 shared, 0 source-specific" in rendered
    assert "- Effects: 0 shared, 2 source-specific; severity medium=1, low=1" in rendered
    assert "- Studio recording: 2 claims, 1 source-specific" in rendered
    assert "### Instrumentation" in rendered
    assert "#### Studio recording" in rendered
    assert "- Guitar 1: Open voicings" in rendered
    assert "### Effects" in rendered
    assert "#### Live performance" in rendered
    assert "- Effects: Dryer signal" in rendered
    assert "#### Studio recording" in rendered
    assert "- Effects: Light chorus" in rendered
    assert "#### Differences" in rendered
    assert "- Studio recording:\n  - Effects: Light chorus [medium severity]" in rendered
    assert "- Live performance:\n  - Effects: Dryer signal [low severity]" in rendered
    assert "- Studio recording:\n  - Guitar 1: Open voicings" not in rendered


def test_recording_source_comparison_uses_unscoped_claims_for_all_recordings() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {
                "studio": "Studio recording",
                "remaster": "Remaster",
            },
            "structured_recording_notes": {
                "Bass": {
                    "category": "performance",
                    "notes": ["Walks to D/F#"],
                },
            },
        }
    )

    rendered = render_text(chart)

    assert "RECORDING SOURCE COMPARISON" in rendered
    assert "Summary" in rendered
    assert "Performance: 1 shared, 0 source-specific" in rendered
    assert "Performance" in rendered
    assert "Studio recording\n- Bass: Walks to D/F#" in rendered
    assert "Remaster\n- Bass: Walks to D/F#" in rendered
    assert "No source-specific differences" in rendered


def test_recording_source_comparison_uses_note_category_override() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Effects": {
                    "category": "effects",
                    "notes": [
                        {
                            "text": "Tape hiss",
                            "category": "source_quality",
                            "severity": "high",
                            "recording_id": "studio",
                        }
                    ],
                },
            },
        }
    )

    rendered = render_markdown(chart)

    assert "### Source Quality" in rendered
    assert "- Effects: Tape hiss [high severity]" in rendered


def test_recording_note_semantics_render_in_research_mode() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Estimated tuning": {
                    "value": {
                        "text": "Standard, approximately 15 cents flat",
                        "claim_origin": "inferred",
                        "confidence": "medium",
                        "provenance": {
                            "source_type": "audio",
                            "source_name": "Example recording",
                            "method": "tuner comparison",
                            "confidence": "medium",
                            "verification_status": "unverified",
                            "claim_origin": "inferred",
                        },
                    }
                },
            },
        }
    )

    rendered = render_text(chart, provenance_mode="research")

    assert "Standard, approximately 15 cents flat (inferred, Medium confidence)" in rendered
    assert "Source type: audio" in rendered
    assert "Source: Example recording" in rendered
    assert "Method: tuner comparison" in rendered


def test_flat_recording_notes_keep_legacy_rendering() -> None:
    chart = SongChart.from_mapping({"title": "Test", "recording_notes": ["Flat note"]})

    assert "## Recording Notes\n\n- Flat note" in render_markdown(chart)
    assert "Recording Notes:\n\n- Flat note" in render_text(chart)


def test_example_markdown_matches_golden_snapshot() -> None:
    chart = load_song_chart(ROOT / "examples" / "open-string-progression.yaml")

    assert render_markdown(chart) == (SNAPSHOTS / "open-string-progression.md").read_text(
        encoding="utf-8"
    )


def test_example_text_matches_golden_snapshot() -> None:
    chart = load_song_chart(ROOT / "examples" / "open-string-progression.yaml")

    assert render_text(chart) == (SNAPSHOTS / "open-string-progression.txt").read_text(
        encoding="utf-8"
    )


def test_example_json_matches_golden_snapshot() -> None:
    chart = load_song_chart(ROOT / "examples" / "open-string-progression.yaml")

    assert song_chart_to_json(chart) == (SNAPSHOTS / "open-string-progression.json").read_text(
        encoding="utf-8"
    )
