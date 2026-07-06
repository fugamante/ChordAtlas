from io import StringIO

from chordatlas.release import run_release_check


def test_release_check_requires_source_root(tmp_path) -> None:
    stderr = StringIO()

    assert run_release_check(tmp_path, stderr=stderr) == 1

    assert "must run from the ChordAtlas source root" in stderr.getvalue()


def test_release_check_reports_schema_mirror_drift(tmp_path) -> None:
    (tmp_path / "src" / "chordatlas").mkdir(parents=True)
    (tmp_path / "tests" / "snapshots").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "song-chart.schema.json").write_text("{}", encoding="utf-8")
    stderr = StringIO()

    assert run_release_check(tmp_path, stderr=stderr) == 1

    output = stderr.getvalue()
    assert "[release-check] schema mirror check" in output
    assert "Schema mirror drift detected" in output
    assert "[release-check] failed: schema mirror check" in output
