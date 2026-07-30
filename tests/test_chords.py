import pytest

from chordatlas.chords import get_chord_shape, require_chord_shape
from chordatlas.models import ChordShape
from chordatlas.render import render_chord_shape


def test_lookup_common_slash_chord() -> None:
    shape = require_chord_shape("D/F#")

    assert shape.frets == ("2", "x", "0", "2", "3", "2")
    assert render_chord_shape(shape).splitlines()[0] == "e|--2--"


def test_unknown_chord_returns_none() -> None:
    assert get_chord_shape("G13sus") is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frets", ("0",), "ChordShape.frets must define exactly six strings"),
        ("tuning", ("D",), "ChordShape.tuning must define exactly six strings"),
        (
            "fingers",
            ("1",),
            "ChordShape.fingers must define exactly six strings when provided",
        ),
    ],
)
def test_chord_shape_requires_six_parallel_string_values(
    field, value, message
) -> None:
    values = {
        "name": "Invalid",
        "frets": ("0", "0", "0", "0", "0", "0"),
        "tuning": ("E", "A", "D", "G", "B", "e"),
    }
    values[field] = value

    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        ChordShape(**values)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "ChordShape.frets is required"),
        ({"frets": ["0"]}, "ChordShape.frets must define exactly six strings"),
        (
            {"frets": ["0"] * 6, "tuning": ["D"]},
            "ChordShape.tuning must define exactly six strings",
        ),
        (
            {"frets": ["0"] * 6, "fingers": []},
            "ChordShape.fingers must define exactly six strings when provided",
        ),
    ],
)
def test_chord_shape_mapping_reports_invalid_parallel_values(value, message) -> None:
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        ChordShape.from_mapping("Invalid", value)


def test_render_chord_shape_uses_stored_tuning() -> None:
    shape = ChordShape(
        name="D modal",
        frets=("0", "0", "0", "2", "3", "0"),
        tuning=("D", "A", "D", "G", "A", "D"),
    )

    assert render_chord_shape(shape).splitlines() == [
        "D|--0--",
        "A|--3--",
        "G|--2--",
        "D|--0--",
        "A|--0--",
        "D|--0--",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frets", "012345"),
        ("frets", {str(index): "0" for index in range(6)}),
        ("frets", 1),
        ("tuning", "EADGBe"),
        ("fingers", "123456"),
    ],
)
def test_direct_chord_shape_rejects_non_sequence_vectors(field, value) -> None:
    values = {
        "name": "Invalid",
        "frets": ("0", "0", "0", "0", "0", "0"),
        "tuning": ("E", "A", "D", "G", "B", "e"),
    }
    values[field] = value

    with pytest.raises(ValueError, match=rf"ChordShape\.{field} must define exactly six strings"):
        ChordShape(**values)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            {"frets": ["0"], "provenance": 42},
            "ChordShape.frets must define exactly six strings",
        ),
        (
            {"frets": ["0"] * 6, "fingers": [], "provenance": 42},
            "ChordShape.fingers must define exactly six strings when provided",
        ),
    ],
)
def test_chord_shape_vector_errors_precede_optional_provenance(value, message) -> None:
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        ChordShape.from_mapping("Invalid", value)
