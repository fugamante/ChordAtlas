import pytest

from chordatlas.models import SongChart


def test_used_chords_are_unique_and_ordered() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "sections": [
                {"name": "Intro", "bars": [["G"], ["D/F#"], ["G"]]},
                {"name": "Verse", "bars": [["Em7"], ["D/F#"]]},
            ],
        }
    )

    assert chart.used_chords == ("G", "D/F#", "Em7")


def test_version_history_accepts_string_and_list_changes() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "version_history": [
                {"version": "1.0", "changes": "Initial transcription."},
                {"version": "1.1", "changes": ["Corrected Verse 2.", "Changed Cmaj7 to Cadd9."]},
            ],
        }
    )

    assert chart.version_history[0].version == "1.0"
    assert chart.version_history[0].changes == ("Initial transcription.",)
    assert chart.version_history[1].changes == (
        "Corrected Verse 2.",
        "Changed Cmaj7 to Cadd9.",
    )


def test_version_history_defaults_to_empty_tuple() -> None:
    chart = SongChart.from_mapping({"title": "Test"})

    assert chart.version_history == ()


def test_version_history_rejects_non_sequence() -> None:
    with pytest.raises(ValueError, match="version_history must be a list of entries"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "version_history": {"version": "1.0", "changes": ["Initial transcription."]},
            }
        )


def test_version_history_rejects_entries_without_versions() -> None:
    with pytest.raises(ValueError, match="must include a version"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "version_history": [{"changes": ["Initial transcription."]}],
            }
        )


def test_version_history_rejects_non_sequence_changes() -> None:
    with pytest.raises(ValueError, match="changes must be a string or list"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "version_history": [{"version": "1.0", "changes": None}],
            }
        )


def test_provenance_warnings_include_note_provenance_maps() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "performance_note_provenance": {
                "0": {"source_type": "unknown", "claim_origin": "unknown"}
            },
            "recording_note_provenance": {
                "amp": {"source_type": "unknown", "claim_origin": "unknown"}
            },
        }
    )

    assert chart.provenance_warnings() == (
        "performance_note.0 has unknown provenance",
        "recording_note.amp has unknown provenance",
    )
