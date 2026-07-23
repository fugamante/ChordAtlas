import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from chordatlas.compare import comparison_to_mapping
from chordatlas.models import (
    ChartMeasure,
    ChartSection,
    RecordingNote,
    RecordingNoteGroup,
    RecordingSource,
    SongChart,
)
from chordatlas.provenance import ClaimOrigin, Confidence
from chordatlas.render import render_markdown
from chordatlas.schema import validate_chart_schema


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: SongChart(title=""), "SongChart.title"),
        (lambda: ChartSection(name="", bars=()), "ChartSection.name"),
        (lambda: RecordingSource(id="", title="Studio"), "RecordingSource.id"),
        (lambda: RecordingSource(id="studio", title=""), "RecordingSource.title"),
        (lambda: RecordingNote(text=""), "RecordingNote.text"),
        (
            lambda: RecordingNoteGroup(name="", value=RecordingNote(text="Present")),
            "RecordingNoteGroup.name",
        ),
    ],
)
def test_direct_domain_construction_rejects_empty_schema_text(
    factory: Callable[[], object],
    field: str,
) -> None:
    with pytest.raises(ValueError, match=rf"^{field} must not be empty$"):
        factory()


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"title": ""}, "SongChart.title"),
        (
            {"title": "Test", "sections": [{"name": "", "bars": []}]},
            "ChartSection.name",
        ),
        (
            {"title": "Test", "recordings": {"": "Studio"}},
            "RecordingSource.id",
        ),
        (
            {"title": "Test", "recordings": {"studio": {"title": ""}}},
            "RecordingSource.title",
        ),
        (
            {
                "title": "Test",
                "structured_recording_notes": {
                    "Effects": {"notes": [{"text": ""}]},
                },
            },
            "RecordingNote.text",
        ),
        (
            {
                "title": "Test",
                "structured_recording_notes": {"": ["Present"]},
            },
            "RecordingNoteGroup.name",
        ),
    ],
)
def test_mapping_construction_rejects_empty_schema_text(
    value: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(ValueError, match=rf"^{field} must not be empty$"):
        SongChart.from_mapping(value)


def test_empty_recording_group_name_precedes_empty_content_error() -> None:
    with pytest.raises(ValueError, match=r"^RecordingNoteGroup.name must not be empty$"):
        RecordingNoteGroup.from_mapping({"name": "", "notes": []})

    with pytest.raises(ValueError, match=r"^RecordingNoteGroup.name must not be empty$"):
        RecordingNoteGroup(name="")


def test_direct_recording_note_group_requires_notes_or_value() -> None:
    with pytest.raises(
        ValueError,
        match=r"^Structured recording note groups must include notes or value$",
    ):
        RecordingNoteGroup(
            name="Effects",
            category="effects",
            severity="medium",
            recording_ids=("studio",),
        )


def test_replacing_recording_note_group_cannot_remove_all_content() -> None:
    group = RecordingNoteGroup(
        name="Effects",
        notes=(RecordingNote(text="Dry"),),
    )

    with pytest.raises(
        ValueError,
        match=r"^Structured recording note groups must include notes or value$",
    ):
        replace(group, notes=(), value=None)


def test_falsey_recording_note_value_remains_present_across_outputs() -> None:
    class FalseyRecordingNote(RecordingNote):
        def __bool__(self) -> bool:
            return False

    group = RecordingNoteGroup(
        name="Effects",
        category="effects",
        value=FalseyRecordingNote(text="Dry", recording_ids=("studio",)),
    )
    chart = SongChart(
        title="Test",
        recordings=(RecordingSource(id="studio", title="Studio"),),
        structured_recording_notes=(group,),
    )
    mapping = chart.to_mapping()

    assert mapping["structured_recording_notes"] == [
        {
            "name": "Effects",
            "value": {
                "text": "Dry",
                "recording_ids": ["studio"],
            },
            "category": "effects",
        }
    ]
    assert validate_chart_schema(chart).passed
    assert "Dry [Studio]" in render_markdown(chart)
    assert comparison_to_mapping(chart)["summary"]["claim_count"] == 1


def test_missing_chart_title_precedes_malformed_sections() -> None:
    with pytest.raises(ValueError, match=r"^Song charts must include a title$"):
        SongChart.from_mapping({"sections": "not a section list"})


def test_missing_section_name_precedes_malformed_bars() -> None:
    with pytest.raises(ValueError, match=r"^Chart sections must include a name$"):
        ChartSection.from_mapping({"bars": 1})


def test_schema_text_validation_preserves_whitespace_values() -> None:
    chart = SongChart(
        title=" ",
        sections=(ChartSection(name=" ", bars=()),),
        recordings=(RecordingSource(id=" ", title=" "),),
        structured_recording_notes=(
            RecordingNoteGroup(name=" ", notes=(RecordingNote(text=" "),)),
        ),
    )

    assert chart.title == " "


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


def test_scalar_string_sequences_match_one_item_lists() -> None:
    scalar = SongChart.from_mapping(
        {
            "title": "Test",
            "voicing_notes": "Open position",
            "performance_notes": "Palm mute",
            "recording_notes": "Double tracked",
            "sections": [
                {
                    "name": "Verse",
                    "notes": "Build gradually",
                    "bars": ["Am", {"chords": "G"}],
                }
            ],
        }
    )
    listed = SongChart.from_mapping(
        {
            "title": "Test",
            "voicing_notes": ["Open position"],
            "performance_notes": ["Palm mute"],
            "recording_notes": ["Double tracked"],
            "sections": [
                {
                    "name": "Verse",
                    "notes": ["Build gradually"],
                    "bars": [["Am"], {"chords": ["G"]}],
                }
            ],
        }
    )

    assert scalar.to_mapping() == listed.to_mapping()
    assert scalar.sections[0].bars[0].chords == ("Am",)
    assert scalar.sections[0].bars[1].chords == ("G",)


def test_empty_scalar_string_sequence_behavior_is_unchanged() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "voicing_notes": "",
            "performance_notes": "",
            "recording_notes": "",
            "sections": [{"name": "Verse", "notes": "", "bars": [""]}],
        }
    )

    assert chart.voicing_notes == ()
    assert chart.performance_notes == ()
    assert chart.recording_notes == ()
    assert chart.sections[0].notes == ()
    assert chart.sections[0].bars == (ChartMeasure(chords=()),)


@pytest.mark.parametrize("value", ["sections", {"Intro": {}}, 1])
def test_sections_require_a_list(value: object) -> None:
    with pytest.raises(ValueError, match=r"^sections must be a list$"):
        SongChart.from_mapping({"title": "Test", "sections": value})


def test_section_items_require_mappings() -> None:
    with pytest.raises(ValueError, match=r"^Chart sections must be a mapping$"):
        SongChart.from_mapping({"title": "Test", "sections": ["Intro"]})


@pytest.mark.parametrize("value", ["G", {"first": ["G"]}, 1])
def test_section_bars_require_a_list(value: object) -> None:
    with pytest.raises(ValueError, match=r"^Chart section bars must be a list$"):
        ChartSection.from_mapping({"name": "Intro", "bars": value})


def test_mapping_measure_requires_chords() -> None:
    with pytest.raises(ValueError, match=r"^Chart measure mappings must include chords$"):
        ChartMeasure.from_value({"timestamp": "00:01"})


@pytest.mark.parametrize("value", [{"first": "G"}, 1])
def test_measure_chords_reject_wrong_containers(value: object) -> None:
    with pytest.raises(ValueError, match=r"^Chart measure chords must be a list$"):
        ChartMeasure.from_value({"chords": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("voicing_notes", {"first": "Open"}),
        ("performance_notes", 1),
        ("recording_notes", {"first": "Double tracked"}),
    ],
)
def test_top_level_note_fields_reject_wrong_containers(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"^{field} must be a list$"):
        SongChart.from_mapping({"title": "Test", field: value})


def test_section_notes_reject_mapping_container() -> None:
    with pytest.raises(ValueError, match=r"^Chart section notes must be a list$"):
        ChartSection.from_mapping({"name": "Intro", "notes": {"first": "Build"}})


@pytest.mark.parametrize("value", [[], "roman"])
def test_analysis_requires_a_mapping(value: object) -> None:
    with pytest.raises(ValueError, match=r"^analysis must be a mapping$"):
        SongChart.from_mapping({"title": "Test", "analysis": value})


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("roman", "I", ["I"]),
        ("nashville", 1, [1]),
        ("roman", 2.5, [2.5]),
        ("nashville", False, [False]),
        ("roman", ("I", "V"), ["I", "V"]),
    ],
)
def test_known_analysis_fields_normalize_scalar_lists(
    key: str,
    value: object,
    expected: list[object],
) -> None:
    chart = SongChart.from_mapping({"title": "Test", "analysis": {key: value}})

    assert chart.analysis[key] == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "analysis.roman must be a scalar or list of scalars"),
        ({"I": 1}, "analysis.roman must be a scalar or list of scalars"),
        (b"I", "analysis.roman must be a scalar or list of scalars"),
        (["I", ["V"]], "analysis.roman entries must be strings, numbers, or booleans"),
        (["I", None], "analysis.roman entries must be strings, numbers, or booleans"),
    ],
)
def test_known_analysis_fields_reject_invalid_values(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=rf"^{message}$"):
        SongChart.from_mapping({"title": "Test", "analysis": {"roman": value}})


def test_analysis_extensions_and_direct_construction_remain_unchanged() -> None:
    extensions = {
        "custom_mapping": {"nested": True},
        "custom_null": None,
        "custom_list": [["nested"], {"free": "form"}],
    }
    mapped = SongChart.from_mapping({"title": "Test", "analysis": extensions})
    direct = SongChart(title="Test", analysis={"roman": "I", **extensions})

    assert mapped.analysis == extensions
    assert "roman" not in mapped.analysis
    assert "nashville" not in mapped.analysis
    assert direct.analysis["roman"] == "I"
    assert {key: direct.analysis[key] for key in extensions} == extensions


def test_analysis_extensions_normalize_recursive_json_native_values() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "analysis": {
                "custom": (
                    None,
                    True,
                    1,
                    1.5,
                    "text",
                    {"nested": [2, 3.5]},
                )
            },
        }
    )

    assert chart.analysis == {
        "custom": [None, True, 1, 1.5, "text", {"nested": [2, 3.5]}]
    }


@pytest.mark.parametrize(
    ("analysis", "message"),
    [
        ({"roman": [float("nan")]}, "analysis.roman[0] must be finite"),
        (
            {"custom": [{"value": float("inf")}]},
            "analysis.custom[0].value must be finite",
        ),
        ({"custom": float("-inf")}, "analysis.custom must be finite"),
        (
            {"custom": b"not-json"},
            "analysis.custom must contain only JSON-compatible values",
        ),
        (
            {"custom": date(2026, 7, 22)},
            "analysis.custom must contain only JSON-compatible values",
        ),
        ({1: "value"}, "analysis keys must be strings"),
        ({"custom": {1: "value"}}, "analysis.custom keys must be strings"),
    ],
)
def test_analysis_rejects_non_json_values_with_deterministic_paths(
    analysis: dict[object, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        SongChart.from_mapping({"title": "Test", "analysis": analysis})


def test_analysis_rejects_recursive_mapping_at_reentry_path() -> None:
    recursive: dict[str, object] = {}
    recursive["loop"] = recursive

    with pytest.raises(
        ValueError,
        match=r"^analysis\.loop contains a recursive container$",
    ):
        SongChart.from_mapping({"title": "Test", "analysis": recursive})


def test_analysis_rejects_recursive_sequence_at_reentry_path() -> None:
    recursive: list[object] = []
    recursive.append(recursive)

    with pytest.raises(
        ValueError,
        match=r"^analysis\.custom\[0\] contains a recursive container$",
    ):
        SongChart.from_mapping(
            {"title": "Test", "analysis": {"custom": recursive}}
        )


def test_analysis_rejects_excessive_nesting_before_python_recursion() -> None:
    analysis: dict[str, object] = {}
    cursor = analysis
    for _ in range(300):
        nested: dict[str, object] = {}
        cursor["next"] = nested
        cursor = nested

    with pytest.raises(
        ValueError,
        match=r"^analysis exceeds maximum nesting depth 256$",
    ):
        SongChart.from_mapping({"title": "Test", "analysis": analysis})


def test_analysis_preserves_supported_deep_nesting() -> None:
    analysis: dict[str, object] = {}
    cursor = analysis
    for _ in range(100):
        nested: dict[str, object] = {}
        cursor["next"] = nested
        cursor = nested

    chart = SongChart.from_mapping({"title": "Test", "analysis": analysis})

    assert chart.analysis["next"]


def test_analysis_accepts_shared_nonrecursive_aliases_as_independent_copies() -> None:
    shared = {"value": [1, 2]}
    chart = SongChart.from_mapping(
        {"title": "Test", "analysis": {"left": shared, "right": shared}}
    )

    assert chart.analysis == {"left": {"value": [1, 2]}, "right": {"value": [1, 2]}}
    assert chart.analysis["left"] is not chart.analysis["right"]


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ("simple", "analysis.simple"),
        ("a.b", 'analysis["a.b"]'),
        ("x[0]", 'analysis["x[0]"]'),
        ("", 'analysis[""]'),
        ('say"hi', 'analysis["say\\"hi"]'),
    ],
)
def test_analysis_diagnostic_paths_quote_non_simple_keys(key: str, path: str) -> None:
    with pytest.raises(ValueError, match=rf"^{re.escape(path)} must be finite$"):
        SongChart.from_mapping(
            {"title": "Test", "analysis": {key: float("nan")}}
        )


def test_provenance_maps_require_mappings() -> None:
    with pytest.raises(ValueError, match=r"^Provenance maps must be a mapping$"):
        SongChart.from_mapping({"title": "Test", "metadata_provenance": []})


@pytest.mark.parametrize(
    "field",
    [
        "metadata_provenance",
        "chord_provenance",
        "analysis_provenance",
        "performance_note_provenance",
        "recording_note_provenance",
    ],
)
def test_provenance_maps_reject_canonical_key_collisions(field: str) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{field} keys 1 and '1' both normalize to '1'$",
    ):
        SongChart.from_mapping(
            {
                "title": "Test",
                field: {
                    1: {"source_type": "audio"},
                    "1": 42,
                },
            }
        )


def test_provenance_maps_preserve_unique_non_string_key_normalization() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "metadata_provenance": {
                1: {"source_type": "audio"},
                2: {"source_type": "contributor"},
            },
        }
    )

    assert list(chart.metadata_provenance) == ["1", "2"]


def test_provenance_map_collision_diagnostics_escape_unsafe_key_repr() -> None:
    class UnsafeKey:
        def __init__(self, label: str, *, broken_repr: bool = False) -> None:
            self.label = label
            self.broken_repr = broken_repr

        def __hash__(self) -> int:
            return id(self)

        def __str__(self) -> str:
            return "same"

        def __repr__(self) -> str:
            if self.broken_repr:
                raise RuntimeError("private repr failure")
            return self.label + "\nINJECTED"

    with pytest.raises(ValueError) as caught:
        SongChart.from_mapping(
            {
                "title": "Test",
                "metadata_provenance": {
                    UnsafeKey("first"): {"source_type": "audio"},
                    UnsafeKey("second", broken_repr=True): {
                        "source_type": "contributor"
                    },
                },
            }
        )

    message = str(caught.value)
    assert "first\\nINJECTED" in message
    assert "<UnsafeKey>" in message
    assert "private repr failure" not in message
    assert "\n" not in message


def test_provenance_map_reports_earliest_collision_before_later_bad_key() -> None:
    class BadStringKey:
        def __hash__(self) -> int:
            return id(self)

        def __str__(self) -> str:
            raise RuntimeError("later normalization failure")

    with pytest.raises(
        ValueError,
        match=r"^metadata_provenance keys 1 and '1' both normalize to '1'$",
    ):
        SongChart.from_mapping(
            {
                "title": "Test",
                "metadata_provenance": {
                    1: {"source_type": "audio"},
                    "1": 42,
                    BadStringKey(): {"source_type": "contributor"},
                },
            }
        )


@pytest.mark.parametrize(
    ("field", "first", "second"),
    [
        (
            "recordings",
            {"title": "First"},
            {"provenance": 42},
        ),
        (
            "structured_recording_notes",
            ["First"],
            {"notes": 42},
        ),
    ],
)
def test_compact_identity_maps_reject_canonical_key_collisions_before_children(
    field: str,
    first: object,
    second: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{field} keys 1 and '1' both normalize to '1'$",
    ):
        SongChart.from_mapping(
            {
                "title": "Test",
                field: {
                    1: first,
                    "1": second,
                },
            }
        )


def test_compact_identity_maps_preserve_unique_non_string_keys() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {1: "First", 2: "Second"},
            "structured_recording_notes": {
                1: ["First note"],
                2: ["Second note"],
            },
        }
    )

    assert [recording.id for recording in chart.recordings] == ["1", "2"]
    assert [group.name for group in chart.structured_recording_notes] == ["1", "2"]


def test_song_chart_from_mapping_requires_mapping() -> None:
    with pytest.raises(ValueError, match=r"^Song charts must be a mapping$"):
        SongChart.from_mapping([])  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, {}, []])
def test_recording_collections_preserve_genuine_empty_defaults(value: object) -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": value,
            "structured_recording_notes": value,
        }
    )

    assert chart.recordings == ()
    assert chart.structured_recording_notes == ()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: SongChart.from_mapping({"title": "Test", "recordings": 0}),
            "recordings must be a list or mapping",
        ),
        (
            lambda: SongChart.from_mapping(
                {"title": "Test", "structured_recording_notes": False}
            ),
            "structured_recording_notes must be a list or mapping",
        ),
        (
            lambda: RecordingSource.from_mapping({"id": "studio", "notes": 0}),
            "recording source notes must be a list",
        ),
        (
            lambda: RecordingNoteGroup.from_mapping(
                {"name": "Effects", "notes": False, "value": "Valid claim"}
            ),
            "recording note group notes must be a string, mapping, or list",
        ),
    ],
)
def test_wrong_falsey_recording_containers_are_rejected(factory, message: str) -> None:
    with pytest.raises(ValueError, match=rf"^{message}$"):
        factory()


@pytest.mark.parametrize("value", [False, 0, ""])
def test_recordings_reject_falsey_noncontainers(value: object) -> None:
    with pytest.raises(ValueError, match=r"^recordings must be a list or mapping$"):
        SongChart.from_mapping({"title": "Test", "recordings": value})


@pytest.mark.parametrize("value", [False, 0, ""])
def test_recording_groups_reject_falsey_noncontainers(value: object) -> None:
    with pytest.raises(
        ValueError,
        match=r"^structured_recording_notes must be a list or mapping$",
    ):
        SongChart.from_mapping({"title": "Test", "structured_recording_notes": value})


def test_mapping_normalizes_optional_schema_strings() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "artist": False,
            "key": 0,
            "source": False,
            "sections": [
                {
                    "name": "Intro",
                    "repeat": 0,
                    "bars": [{"chords": ["G"], "timestamp": False}],
                }
            ],
        }
    )

    assert chart.artist == "False"
    assert chart.key == "0"
    assert chart.source == "False"
    assert chart.sections[0].repeat == "0"
    assert chart.sections[0].bars[0].timestamp == "False"


def test_optional_schema_strings_preserve_none_and_direct_construction() -> None:
    mapped = SongChart.from_mapping(
        {
            "title": "Test",
            "artist": None,
            "key": None,
            "source": None,
            "sections": [
                {
                    "name": "Intro",
                    "repeat": None,
                    "bars": [{"chords": ["G"], "timestamp": None}],
                }
            ],
        }
    )
    direct_measure = ChartMeasure(chords=("G",), timestamp=False)  # type: ignore[arg-type]
    direct_section = ChartSection(
        name="Intro",
        bars=(direct_measure,),
        repeat=0,  # type: ignore[arg-type]
    )
    direct = SongChart(
        title="Test",
        artist=False,  # type: ignore[arg-type]
        key=0,  # type: ignore[arg-type]
        source=False,  # type: ignore[arg-type]
        sections=(direct_section,),
    )

    assert mapped.artist is None
    assert mapped.key is None
    assert mapped.source is None
    assert mapped.sections[0].repeat is None
    assert mapped.sections[0].bars[0].timestamp is None
    assert direct.artist is False
    assert direct.key == 0
    assert direct.source is False
    assert direct.sections[0].repeat == 0
    assert direct.sections[0].bars[0].timestamp is False


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


@pytest.mark.parametrize(("outer", "nested"), [("Effects", "Effects"), (1, 1), (1, "1")])
def test_compact_recording_group_identity_accepts_normalization_equivalent_values(
    outer: object,
    nested: object,
) -> None:
    absent = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {outer: {"notes": ["Light chorus"]}},
        }
    )
    duplicate = SongChart.from_mapping(
        {
            "title": "Test",
            "structured_recording_notes": {
                outer: {"name": nested, "notes": ["Light chorus"]}
            },
        }
    )

    assert duplicate.to_mapping() == absent.to_mapping()
    assert duplicate.structured_recording_notes[0].name == str(outer)


@pytest.mark.parametrize("nested", ["Tuning", None, 2])
def test_compact_recording_group_identity_rejects_conflicts(nested: object) -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"^structured_recording_notes mapping key 'Effects' conflicts with nested "
            r"name .*; remove the nested name or use the explicit list form$"
        ),
    ):
        SongChart.from_mapping(
            {
                "title": "Test",
                "recordings": {"studio": "Studio recording"},
                "structured_recording_notes": {
                    "Effects": {
                        "name": nested,
                        "notes": {"malformed": "before note parsing"},
                        "recording_ids": ["missing"],
                    }
                },
            }
        )


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


def test_recording_scope_aliases_normalize_independently_and_emit_plural() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Group singular": {
                    "recording_id": "studio",
                    "notes": ["Group scope"],
                },
                "Group plural": {
                    "recording_ids": ["studio"],
                    "notes": ["Group scope"],
                },
                "Equivalent aliases": {
                    "recording_ids": ["studio"],
                    "recording_id": "studio",
                    "notes": [
                        {
                            "text": "Note scope",
                            "recording_ids": ["studio"],
                            "recording_id": "studio",
                        }
                    ],
                    "value": {
                        "text": "Value scope",
                        "recording_ids": ("studio",),
                        "recording_id": ["studio"],
                    },
                },
            },
        }
    )

    groups = chart.structured_recording_notes
    assert [group.recording_ids for group in groups] == [
        ("studio",),
        ("studio",),
        ("studio",),
    ]
    assert groups[2].notes[0].recording_ids == ("studio",)
    assert groups[2].value is not None
    assert groups[2].value.recording_ids == ("studio",)
    rendered_groups = chart.to_mapping()["structured_recording_notes"]
    assert all("recording_id" not in group for group in rendered_groups)
    assert "recording_id" not in rendered_groups[2]["notes"][0]
    assert "recording_id" not in rendered_groups[2]["value"]


@pytest.mark.parametrize("location", ["group", "note", "value"])
@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        (["studio"], "live"),
        (["studio"], None),
        (["studio"], 1),
    ],
)
def test_recording_scope_aliases_reject_conflicts_before_sibling_parsing(
    location: str,
    plural: object,
    singular: object,
) -> None:
    group: dict[str, object] = {
        "notes": ["Valid note"],
        "provenance": 42,
    }
    if location == "group":
        target = group
        path = "structured_recording_notes['Effects']"
    elif location == "note":
        target = {
            "text": "Scoped note",
            "confidence": "invalid",
            "provenance": 42,
        }
        group["notes"] = [target]
        path = "structured_recording_notes['Effects'].notes[0]"
    else:
        target = {
            "text": "Scoped value",
            "confidence": "invalid",
            "provenance": 42,
        }
        group = {"value": target, "provenance": 42}
        path = "structured_recording_notes['Effects'].value"
    target["recording_ids"] = plural
    target["recording_id"] = singular
    message = (
        f"{path} has conflicting recording scope aliases: "
        f"recording_ids={plural!r}, recording_id={singular!r}; keep only recording_ids"
    )

    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "recordings": {"studio": "Studio", "live": "Live"},
                "structured_recording_notes": {"Effects": group},
            }
        )


@pytest.mark.parametrize("key", ["recording_ids", "recording_id"])
def test_recording_scope_aliases_reject_null_when_authored_alone(key: str) -> None:
    message = (
        f"structured_recording_notes['Effects'].{key}=None must be a string or list; "
        "keep only recording_ids"
    )

    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        SongChart.from_mapping(
            {
                "title": "Test",
                "structured_recording_notes": {
                    "Effects": {key: None, "notes": ["Light chorus"]}
                },
            }
        )


def test_recording_scope_arbitration_follows_required_group_and_note_identity() -> None:
    aliases = {"recording_ids": ["studio"], "recording_id": "live"}

    with pytest.raises(
        ValueError,
        match=r"^Structured recording note groups must include a name$",
    ):
        RecordingNoteGroup.from_mapping({**aliases, "notes": ["Light chorus"]})
    with pytest.raises(
        ValueError,
        match=r"^Structured recording note entries must include text$",
    ):
        RecordingNote.from_value(aliases)


def test_empty_note_text_precedes_scope_alias_and_semantic_validation() -> None:
    with pytest.raises(ValueError, match=r"^RecordingNote.text must not be empty$"):
        RecordingNote.from_value(
            {
                "text": "",
                "recording_ids": ["studio"],
                "recording_id": "live",
                "confidence": "invalid",
            }
        )


@pytest.mark.parametrize(("outer", "nested"), [("studio", "studio"), (1, 1), (1, "1")])
def test_compact_recording_identity_accepts_normalization_equivalent_values(
    outer: object,
    nested: object,
) -> None:
    absent = SongChart.from_mapping(
        {"title": "Test", "recordings": {outer: {"title": "Studio recording"}}}
    )
    duplicate = SongChart.from_mapping(
        {
            "title": "Test",
            "recordings": {outer: {"id": nested, "title": "Studio recording"}},
        }
    )

    assert duplicate.to_mapping() == absent.to_mapping()
    assert duplicate.recordings[0].id == str(outer)


@pytest.mark.parametrize("nested", ["remaster", None, 2])
def test_compact_recording_identity_rejects_conflicts(nested: object) -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"^recordings mapping key 'studio' conflicts with nested id .*; "
            r"remove the nested id or use the explicit list form$"
        ),
    ):
        SongChart.from_mapping(
            {
                "title": "Test",
                "recordings": {
                    "studio": {
                        "id": nested,
                        "title": "Studio recording",
                        "provenance": 42,
                    }
                },
            }
        )


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
