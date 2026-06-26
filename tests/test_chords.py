from chordatlas.chords import get_chord_shape, require_chord_shape
from chordatlas.render import render_chord_shape


def test_lookup_common_slash_chord() -> None:
    shape = require_chord_shape("D/F#")

    assert shape.frets == ("2", "x", "0", "2", "3", "2")
    assert render_chord_shape(shape).splitlines()[0] == "e|--2--"


def test_unknown_chord_returns_none() -> None:
    assert get_chord_shape("G13sus") is None

