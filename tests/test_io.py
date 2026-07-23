import json
from pathlib import Path

import pytest
import yaml

from chordatlas.io import example_song_yaml, load_song_chart, song_chart_to_json
from chordatlas.models import SongChart

ROOT = Path(__file__).resolve().parents[1]


def test_example_song_yaml_matches_current_schema() -> None:
    chart = yaml.safe_load(example_song_yaml())

    assert chart["version"] == "2.0"
    assert chart["version_history"][-1] == {
        "version": "2.0",
        "changes": ["Verified against isolated stems."],
    }
    assert chart["provenance"]["source_name"] == "Starter chart"
    assert chart["chord_provenance"]["Cadd9"]["source_name"] == "Example recording"


def test_example_file_matches_starter_yaml() -> None:
    starter = yaml.safe_load(example_song_yaml())
    example = yaml.safe_load((ROOT / "examples" / "open-string-progression.yaml").read_text())

    assert example == starter


def test_load_song_chart_rejects_non_mapping_yaml(tmp_path) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Expected YAML mapping"):
        load_song_chart(chart_path)


def test_load_song_chart_contains_malformed_yaml(tmp_path) -> None:
    chart_path = tmp_path / "malformed.yaml"
    chart_path.write_text("title: [\n", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"^Invalid YAML in {chart_path}$"):
        load_song_chart(chart_path)


def test_load_song_chart_contains_yaml_parser_recursion(tmp_path, monkeypatch) -> None:
    chart_path = tmp_path / "deep.yaml"
    chart_path.write_text("title: Test\n", encoding="utf-8")

    def recurse(handle, *, Loader) -> None:
        raise RecursionError

    monkeypatch.setattr(yaml, "load", recurse)

    with pytest.raises(
        ValueError,
        match=rf"^YAML in {chart_path} exceeds the safe nesting depth$",
    ):
        load_song_chart(chart_path)


@pytest.mark.parametrize(
    ("chart_yaml", "key", "line"),
    [
        ("title: First\ntitle: Second\n", "title", 2),
        (
            "title: Test\nrecordings:\n  studio: First\n  studio: Second\n",
            "studio",
            4,
        ),
        (
            "title: Test\nstructured_recording_notes:\n  Effects: [First]\n  Effects: [Second]\n",
            "Effects",
            4,
        ),
        (
            "title: Test\nstructured_recording_notes:\n  Effects:\n    notes:\n"
            "      - text: First\n        text: Second\n",
            "text",
            6,
        ),
    ],
)
def test_load_song_chart_rejects_duplicate_yaml_keys(
    tmp_path, chart_yaml, key, line
) -> None:
    chart_path = tmp_path / "duplicate.yaml"
    chart_path.write_text(chart_yaml, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=rf"^Duplicate YAML key {key!r} in {chart_path} at line {line}$",
    ):
        load_song_chart(chart_path)


def test_load_song_chart_preserves_yaml_merge_overrides(tmp_path) -> None:
    chart_path = tmp_path / "merge.yaml"
    chart_path.write_text(
        "title: Test\n"
        "structured_recording_notes:\n"
        "  - name: Base\n"
        "    value: &base\n"
        "      text: Base\n"
        "  - name: Override\n"
        "    value:\n"
        "      <<: *base\n"
        "      text: Override\n",
        encoding="utf-8",
    )

    chart = load_song_chart(chart_path)

    assert [group.value.text for group in chart.structured_recording_notes] == [
        "Base",
        "Override",
    ]


def test_load_song_chart_preserves_yaml_merge_sequences(tmp_path) -> None:
    chart_path = tmp_path / "merge-sequence.yaml"
    chart_path.write_text(
        "title: Test\n"
        "base_a: &a {text: Base}\n"
        "base_b: &b {confidence: high}\n"
        "structured_recording_notes:\n"
        "  - name: Merged\n"
        "    value:\n"
        "      <<: [*a, *b]\n",
        encoding="utf-8",
    )

    chart = load_song_chart(chart_path)

    assert chart.structured_recording_notes[0].value.text == "Base"


def test_load_song_chart_reports_earliest_nested_duplicate(tmp_path) -> None:
    chart_path = tmp_path / "duplicate-order.yaml"
    chart_path.write_text(
        "title: Test\n"
        "analysis:\n"
        "  custom:\n"
        "    x: first\n"
        "    x: second\n"
        "artist: First\n"
        "artist: Second\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=rf"^Duplicate YAML key 'x' in {chart_path} at line 5$",
    ):
        load_song_chart(chart_path)


def test_load_song_chart_rejects_repeated_merge_keys(tmp_path) -> None:
    chart_path = tmp_path / "duplicate-merge.yaml"
    chart_path.write_text(
        "title: Test\n"
        "base_a: &a {text: Base}\n"
        "base_b: &b {confidence: high}\n"
        "structured_recording_notes:\n"
        "  - name: Merged\n"
        "    value:\n"
        "      <<: *a\n"
        "      <<: *b\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=rf"^Duplicate YAML key '<<' in {chart_path} at line 8$",
    ):
        load_song_chart(chart_path)


def test_load_song_chart_allows_same_key_in_distinct_mappings(tmp_path) -> None:
    chart_path = tmp_path / "repeated-values.yaml"
    chart_path.write_text(
        "title: Test\n"
        "structured_recording_notes:\n"
        "  - name: First\n"
        "    value: {text: One}\n"
        "  - name: Second\n"
        "    value: {text: Two}\n",
        encoding="utf-8",
    )

    chart = load_song_chart(chart_path)

    assert [group.value.text for group in chart.structured_recording_notes] == ["One", "Two"]


def test_load_song_chart_rejects_malformed_version_history(tmp_path) -> None:
    chart_path = tmp_path / "bad-history.yaml"
    chart_path.write_text(
        'title: Test\nversion_history: {version: "1.0", changes: ["Initial"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version_history must be a list of entries"):
        load_song_chart(chart_path)


def test_load_song_chart_rejects_mapping_sections_container(tmp_path) -> None:
    chart_path = tmp_path / "bad-sections.yaml"
    chart_path.write_text("title: Test\nsections: {Intro: {bars: [[G]]}}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"^sections must be a list$"):
        load_song_chart(chart_path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_song_chart_json_rejects_nonfinite_direct_construction(value: float) -> None:
    chart = SongChart(title="Test", analysis={"custom": value})

    with pytest.raises(ValueError, match="Out of range float values"):
        song_chart_to_json(chart)


def test_song_chart_json_remains_strictly_machine_readable() -> None:
    rendered = song_chart_to_json(
        SongChart.from_mapping(
            {"title": "Test", "analysis": {"custom": [None, True, 1, 1.5]}}
        )
    )

    assert json.loads(rendered, parse_constant=lambda value: pytest.fail(value))[
        "analysis"
    ] == {"custom": [None, True, 1, 1.5]}
