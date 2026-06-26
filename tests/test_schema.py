import json
from pathlib import Path

from jsonschema import Draft202012Validator

from chordatlas.io import load_song_chart, song_chart_to_json


SCHEMA_PATH = Path("schemas/song-chart.schema.json")


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _export_payload(path: Path) -> dict:
    chart = load_song_chart(path)
    return json.loads(song_chart_to_json(chart))


def test_song_chart_schema_is_valid_json_schema() -> None:
    Draft202012Validator.check_schema(_schema())


def test_example_yaml_export_matches_song_chart_schema() -> None:
    schema = _schema()
    payload = _export_payload(Path("examples/open-string-progression.yaml"))

    Draft202012Validator(schema).validate(payload)


def test_simple_yaml_export_matches_song_chart_schema(tmp_path) -> None:
    chart_path = tmp_path / "simple.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Simple Chart",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G, D/F#]",
                "      - [Em7]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = _export_payload(chart_path)

    assert payload["sections"][0]["bars"] == [["G", "D/F#"], ["Em7"]]
    Draft202012Validator(_schema()).validate(payload)
