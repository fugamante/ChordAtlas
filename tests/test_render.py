from pathlib import Path

from chordatlas.io import load_song_chart
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
