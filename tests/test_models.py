import pytest

from chordatlas.models import SongChart
from chordatlas.provenance import ClaimOrigin, Confidence


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


def test_song_chart_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        SongChart.from_mapping({"schema_version": "2.0.0", "title": "Test"})


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


def test_structured_recording_notes_accept_compact_mapping() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Guitar 1": ["Left channel", "Acoustic", "Open voicings"],
                "Estimated tuning": "Standard, approximately 15 cents flat",
            },
        }
    )

    assert chart.structured_recording_notes[0].name == "Guitar 1"
    assert tuple(note.text for note in chart.structured_recording_notes[0].notes) == (
        "Left channel",
        "Acoustic",
        "Open voicings",
    )
    assert chart.structured_recording_notes[1].name == "Estimated tuning"
    assert chart.structured_recording_notes[1].value is not None
    assert chart.structured_recording_notes[1].value.text == (
        "Standard, approximately 15 cents flat"
    )


def test_structured_recording_notes_accept_explicit_list() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": [
                {"name": "Effects", "notes": ["Light chorus", "Spring reverb"]},
                {"name": "Estimated tuning", "value": "Standard"},
            ],
        }
    )

    assert chart.to_mapping()["structured_recording_notes"] == [
        {"name": "Effects", "notes": ["Light chorus", "Spring reverb"]},
        {"name": "Estimated tuning", "value": "Standard"},
    ]


def test_structured_recording_notes_accept_note_level_semantics() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Effects": [
                    {
                        "text": "Light chorus",
                        "category": "effects",
                        "severity": "medium",
                        "claim_origin": "inferred",
                        "confidence": "medium",
                        "provenance": {
                            "source_type": "audio",
                            "method": "human-ear transcription",
                            "confidence": "medium",
                            "verification_status": "unverified",
                            "claim_origin": "inferred",
                        },
                    }
                ],
            },
        }
    )

    note = chart.structured_recording_notes[0].notes[0]
    assert note.text == "Light chorus"
    assert note.category == "effects"
    assert note.severity == "medium"
    assert note.claim_origin is ClaimOrigin.INFERRED
    assert note.confidence is Confidence.MEDIUM
    assert note.provenance[0].method == "human-ear transcription"
    assert chart.to_mapping()["structured_recording_notes"] == [
        {
            "name": "Effects",
            "notes": [
                {
                    "text": "Light chorus",
                    "category": "effects",
                    "severity": "medium",
                    "claim_origin": "inferred",
                    "confidence": "medium",
                    "provenance": [
                        {
                            "source_type": "audio",
                            "method": "human-ear transcription",
                            "confidence": "medium",
                            "verification_status": "unverified",
                            "claim_origin": "inferred",
                        }
                    ],
                }
            ],
        }
    ]


def test_recordings_accept_compact_mapping_and_note_scopes() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {
                "studio": {
                    "title": "Studio recording",
                    "source_url": "https://example.invalid/studio",
                    "version_label": "Album version",
                },
                "live": "Live performance",
            },
            "structured_recording_notes": {
                "Guitar 1": {
                    "category": "instrumentation",
                    "severity": "medium",
                    "recording_ids": ["studio"],
                    "notes": [
                        {
                            "text": "Left channel",
                            "claim_origin": "observed",
                            "confidence": "high",
                            "recording_id": "studio",
                        }
                    ],
                },
                "Bass": {
                    "recording_ids": ["studio", "live"],
                    "notes": ["Walks to D/F#"],
                },
            },
        }
    )

    assert chart.recordings[0].id == "studio"
    assert chart.recordings[0].title == "Studio recording"
    assert chart.recordings[0].source_url == "https://example.invalid/studio"
    assert chart.recordings[1].id == "live"
    assert chart.recordings[1].title == "Live performance"
    assert chart.structured_recording_notes[0].recording_ids == ("studio",)
    assert chart.structured_recording_notes[0].category == "instrumentation"
    assert chart.structured_recording_notes[0].severity == "medium"
    assert chart.structured_recording_notes[0].notes[0].recording_ids == ("studio",)
    assert chart.structured_recording_notes[1].recording_ids == ("studio", "live")
    assert chart.to_mapping()["recordings"] == [
        {
            "id": "studio",
            "title": "Studio recording",
            "source_url": "https://example.invalid/studio",
            "version_label": "Album version",
        },
        {"id": "live", "title": "Live performance"},
    ]


def test_recording_references_require_declared_recordings() -> None:
    with pytest.raises(ValueError, match="recording_ids require matching recordings"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "structured_recording_notes": {
                    "Guitar 1": {
                        "recording_ids": ["studio"],
                        "notes": ["Left channel"],
                    },
                },
            }
        )


def test_recording_references_reject_unknown_ids() -> None:
    with pytest.raises(ValueError, match="Unknown recording_id"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "recordings": {"studio": "Studio recording"},
                "structured_recording_notes": {
                    "Guitar 1": {
                        "recording_ids": ["live"],
                        "notes": ["Left channel"],
                    },
                },
            }
        )


def test_recordings_reject_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="Duplicate recording id"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "recordings": [
                    {"id": "studio", "title": "Studio recording"},
                    {"id": "studio", "title": "Alternate studio recording"},
                ],
            }
        )


def test_structured_recording_notes_reject_empty_groups() -> None:
    with pytest.raises(ValueError, match="must include notes or value"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "structured_recording_notes": [{"name": "Guitar 1"}],
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


def test_provenance_warnings_include_recording_note_records() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                "Effects": [
                    {
                        "text": "Unknown effect",
                        "provenance": {"source_type": "unknown", "claim_origin": "unknown"},
                    }
                ],
                "Estimated tuning": {
                    "value": {
                        "text": "Flat",
                        "provenance": {"source_type": "unknown", "claim_origin": "unknown"},
                    }
                },
            },
        }
    )

    assert chart.provenance_warnings() == (
        "recording_group.0.note.0 has unknown provenance",
        "recording_group.1.value has unknown provenance",
    )
