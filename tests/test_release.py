import sys
from io import StringIO
from pathlib import Path

import pytest

import chordatlas.release as release
from chordatlas.release import run_release_check
from chordatlas.snapshots import SnapshotResult


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


def test_release_check_reports_missing_snapshot_as_drift(tmp_path, monkeypatch) -> None:
    _make_source_root(tmp_path)
    monkeypatch.setattr(release, "_check_schema_mirror", lambda root, stderr: 0)
    monkeypatch.setattr(
        release,
        "check_snapshots",
        lambda root: SnapshotResult(
            status="dirty",
            root=tmp_path,
            target="all",
            checked=("missing.md",),
            drift=("missing.md",),
        ),
    )
    stderr = StringIO()

    assert run_release_check(tmp_path, stderr=stderr) == 1

    output = stderr.getvalue()
    assert "Snapshot drift detected:" in output
    assert "- missing.md" in output
    assert "[release-check] error:" not in output


def test_release_check_contains_unexpected_step_error(tmp_path, monkeypatch) -> None:
    _make_source_root(tmp_path)
    monkeypatch.setattr(release, "_check_schema_mirror", lambda root, stderr: 0)

    def fail_snapshot(root: Path, stderr: StringIO) -> int:
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(release, "_verify_snapshots", fail_snapshot)
    stderr = StringIO()

    assert run_release_check(tmp_path, stderr=stderr) == 1

    assert stderr.getvalue().splitlines()[-2:] == [
        "[release-check] error: snapshot verification: OSError: snapshot unavailable",
        "[release-check] failed: snapshot verification",
    ]


def test_release_check_reports_secondary_cleanup_note(tmp_path, monkeypatch) -> None:
    _make_source_root(tmp_path)
    monkeypatch.setattr(release, "_check_schema_mirror", lambda root, stderr: 0)

    def fail_snapshot(root: Path, stderr: StringIO) -> int:
        error = OSError("snapshot unavailable")
        error.add_note("Temporary cleanup failed: cleanup denied")
        raise error

    monkeypatch.setattr(release, "_verify_snapshots", fail_snapshot)
    stderr = StringIO()

    assert run_release_check(tmp_path, stderr=stderr) == 1

    assert "[release-check] note: Temporary cleanup failed: cleanup denied\n" in (
        stderr.getvalue()
    )


def test_release_check_preserves_preexisting_cache_on_failure(tmp_path, monkeypatch) -> None:
    _make_source_root(tmp_path)
    cache = tmp_path / "src" / "chordatlas" / "__pycache__"
    cache.mkdir()
    marker = cache / "operator-state"
    marker.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(release, "_check_schema_mirror", lambda root, stderr: 1)

    assert run_release_check(tmp_path, stderr=StringIO()) == 1

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_compileall_isolates_generated_bytecode(tmp_path) -> None:
    _make_source_root(tmp_path)
    (tmp_path / "src" / "chordatlas" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_module.py").write_text("def test_value(): pass\n", encoding="utf-8")
    cache = tmp_path / "src" / "chordatlas" / "__pycache__"
    cache.mkdir()
    marker = cache / "operator-state"
    marker.write_text("preserve", encoding="utf-8")

    assert release._run_compileall(tmp_path, StringIO()) == 0

    assert marker.read_text(encoding="utf-8") == "preserve"
    assert list(tmp_path.rglob("*.pyc")) == []


class _CleanupFailingDirectory:
    def __init__(self, path: Path) -> None:
        self.name = str(path)
        path.mkdir()

    def cleanup(self) -> None:
        raise OSError("cleanup denied")


def _fail_temp_cleanup(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        release.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: _CleanupFailingDirectory(path),
    )


def test_compileall_preserves_process_control_across_cleanup_failure(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)
    temp_path = tmp_path / "compile-temp"
    _fail_temp_cleanup(monkeypatch, temp_path)
    previous_prefix = sys.pycache_prefix

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("operator stop")

    monkeypatch.setattr(release.compileall, "compile_dir", interrupt)

    with pytest.raises(KeyboardInterrupt, match="operator stop") as caught:
        release._run_compileall(tmp_path, StringIO())

    assert sys.pycache_prefix == previous_prefix
    assert caught.value.__notes__ == [
        f"Temporary cleanup failed: temporary directory {temp_path}: cleanup denied"
    ]
    assert temp_path.is_dir()


def test_compileall_preserves_primary_error_across_cleanup_failure(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)
    temp_path = tmp_path / "compile-temp"
    _fail_temp_cleanup(monkeypatch, temp_path)

    def fail(*args, **kwargs):
        raise OSError("compilation unavailable")

    monkeypatch.setattr(release.compileall, "compile_dir", fail)

    with pytest.raises(OSError, match="compilation unavailable") as caught:
        release._run_compileall(tmp_path, StringIO())

    assert caught.value.__notes__ == [
        f"Temporary cleanup failed: temporary directory {temp_path}: cleanup denied"
    ]
    assert temp_path.is_dir()


def test_compileall_reports_cleanup_failure_after_success(tmp_path, monkeypatch) -> None:
    _make_source_root(tmp_path)
    temp_path = tmp_path / "compile-temp"
    _fail_temp_cleanup(monkeypatch, temp_path)
    monkeypatch.setattr(release.compileall, "compile_dir", lambda *args, **kwargs: True)

    with pytest.raises(OSError) as caught:
        release._run_compileall(tmp_path, StringIO())

    assert str(temp_path) in str(caught.value)
    assert "cleanup denied" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert temp_path.is_dir()


def test_compileall_reports_failed_result_before_cleanup_failure(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)
    temp_path = tmp_path / "compile-temp"
    _fail_temp_cleanup(monkeypatch, temp_path)
    monkeypatch.setattr(release.compileall, "compile_dir", lambda *args, **kwargs: False)
    stderr = StringIO()

    with pytest.raises(OSError) as caught:
        release._run_compileall(tmp_path, stderr)

    assert str(temp_path) in str(caught.value)
    assert "cleanup denied" in str(caught.value)
    assert temp_path.is_dir()
    assert stderr.getvalue() == "Python compilation failed under src/ or tests/.\n"


def test_package_inspection_preserves_process_control_across_cleanup_failure(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)
    temp_path = tmp_path / "release-temp"
    _fail_temp_cleanup(monkeypatch, temp_path)

    def stop(*args, **kwargs):
        raise SystemExit(9)

    monkeypatch.setattr(release, "_run_command", stop)

    with pytest.raises(SystemExit) as caught:
        release._inspect_package_build(tmp_path, StringIO())

    assert caught.value.code == 9
    assert caught.value.__notes__ == [
        f"Temporary cleanup failed: temporary directory {temp_path}: cleanup denied"
    ]
    assert temp_path.is_dir()


def test_pytest_ignores_inherited_selection_and_output_options(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)
    marker = tmp_path / "src" / "chordatlas" / "__pycache__" / "operator-state"
    marker.parent.mkdir()
    marker.write_text("preserve", encoding="utf-8")
    (tmp_path / "tests" / "test_gate.py").write_text(
        "def test_selected(): pass\n\ndef test_required(): assert False\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "PYTEST_ADDOPTS",
        f"-k test_selected --junitxml={marker}",
    )
    monkeypatch.setenv("PYTEST_PLUGINS", "missing_release_plugin")

    assert release._run_pytest(tmp_path, StringIO()) != 0

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_release_check_does_not_catch_process_control_exceptions(
    tmp_path, monkeypatch
) -> None:
    _make_source_root(tmp_path)

    def interrupt(root: Path, stderr: StringIO) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(release, "_check_schema_mirror", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_release_check(tmp_path, stderr=StringIO())


def _make_source_root(root: Path) -> None:
    (root / "src" / "chordatlas").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
