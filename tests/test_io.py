from pathlib import Path

import pytest
import yaml

from chordatlas.io import example_song_yaml, load_song_chart

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


def test_load_song_chart_rejects_malformed_version_history(tmp_path) -> None:
    chart_path = tmp_path / "bad-history.yaml"
    chart_path.write_text(
        'title: Test\nversion_history: {version: "1.0", changes: ["Initial"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version_history must be a list of entries"):
        load_song_chart(chart_path)
