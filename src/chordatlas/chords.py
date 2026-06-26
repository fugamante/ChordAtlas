from __future__ import annotations

from chordatlas.models import ChordShape


_COMMON_SHAPES: dict[str, dict[str, object]] = {
    "A": {"frets": ["x", "0", "2", "2", "2", "0"]},
    "Am": {"frets": ["x", "0", "2", "2", "1", "0"]},
    "A7": {"frets": ["x", "0", "2", "0", "2", "0"]},
    "Bm": {"frets": ["x", "2", "4", "4", "3", "2"], "notes": "Common barre shape."},
    "B7": {"frets": ["x", "2", "1", "2", "0", "2"]},
    "C": {"frets": ["x", "3", "2", "0", "1", "0"]},
    "Cadd9": {"frets": ["x", "3", "2", "0", "3", "3"]},
    "Cmaj7": {"frets": ["x", "3", "2", "0", "0", "0"]},
    "D": {"frets": ["x", "x", "0", "2", "3", "2"]},
    "Dm": {"frets": ["x", "x", "0", "2", "3", "1"]},
    "D7": {"frets": ["x", "x", "0", "2", "1", "2"]},
    "D/F#": {"frets": ["2", "x", "0", "2", "3", "2"], "notes": "Thumb or finger F# bass."},
    "Em": {"frets": ["0", "2", "2", "0", "0", "0"]},
    "Em7": {"frets": ["0", "2", "2", "0", "3", "3"]},
    "E": {"frets": ["0", "2", "2", "1", "0", "0"]},
    "E7": {"frets": ["0", "2", "0", "1", "0", "0"]},
    "F": {"frets": ["1", "3", "3", "2", "1", "1"], "notes": "Common barre shape."},
    "Fmaj7": {"frets": ["x", "x", "3", "2", "1", "0"]},
    "G": {"frets": ["3", "2", "0", "0", "3", "3"]},
    "G/B": {"frets": ["x", "2", "0", "0", "3", "3"]},
    "G7": {"frets": ["3", "2", "0", "0", "0", "1"]},
}


COMMON_CHORD_SHAPES: dict[str, ChordShape] = {
    name: ChordShape.from_mapping(name, shape) for name, shape in _COMMON_SHAPES.items()
}


def get_chord_shape(name: str) -> ChordShape | None:
    return COMMON_CHORD_SHAPES.get(name)


def require_chord_shape(name: str) -> ChordShape:
    shape = get_chord_shape(name)
    if shape is None:
        raise KeyError(f"No built-in guitar shape for chord: {name}")
    return shape

