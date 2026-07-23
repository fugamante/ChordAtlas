import builtins
import io
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import chordatlas.cli as cli
import chordatlas._fs as fs
import chordatlas.snapshots as snapshots
from chordatlas.cli import main
from chordatlas.io import example_song_yaml
from chordatlas.schema import SCHEMA_NAMES, SchemaValidationResult, sync_schema_mirror
from chordatlas.snapshots import check_snapshots

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_EXAMPLE = "examples/research-recording-comparison.yaml"


def _comparison_chart_yaml() -> str:
    return "\n".join(
        [
            "title: Test",
            "recordings:",
            "  studio: Studio recording",
            "  live: Live performance",
            "structured_recording_notes:",
            "  Effects:",
            "    category: effects",
            "    notes:",
            "      - text: Light chorus",
            "        recording_id: studio",
            "        severity: medium",
            "      - text: Dryer amp tone",
            "        recording_id: live",
            "",
        ]
    )


def _provenance_comparison_chart_yaml() -> str:
    return "\n".join(
        [
            "title: Test",
            "recordings:",
            "  studio:",
            "    title: Studio recording",
            "    source_url: https://example.invalid/studio",
            "    version_label: Studio reference",
            "structured_recording_notes:",
            "  Estimated tuning:",
            "    category: tuning",
            "    notes:",
            "      - text: Approximately 15 cents flat",
            "        recording_id: studio",
            "        confidence: medium",
            "        claim_origin: inferred",
            "        provenance:",
            "          source_type: audio",
            "          source_name: Studio recording",
            "          timestamp_range: 00:00-00:10",
            "          method: tuner comparison",
            "          confidence: medium",
            "          verification_status: verified",
            "          claim_origin: inferred",
            "          evidence_refs:",
            "            - ref_id: studio-00-00",
            "",
        ]
    )


def _copy_snapshot_workspace(tmp_path: Path) -> Path:
    (tmp_path / "examples").mkdir()
    (tmp_path / "tests" / "snapshots").mkdir(parents=True)
    for name in ("open-string-progression.yaml", "research-recording-comparison.yaml"):
        shutil.copyfile(ROOT / "examples" / name, tmp_path / "examples" / name)
    for path in (ROOT / "tests" / "snapshots").iterdir():
        if path.is_file():
            shutil.copyfile(path, tmp_path / "tests" / "snapshots" / path.name)
    return tmp_path


def _stub_snapshot_files(*names: str) -> tuple[snapshots.SnapshotFile, ...]:
    return tuple(
        snapshots.SnapshotFile(name, lambda name=name: f"GENERATED {name}\n")
        for name in names
    )


def _prepared_diff_artifacts(
    *names: str,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return tuple((name, f"{name}.diff", (f"DIFF {name}\n",)) for name in names)


def _close_parent_then_fail(real_close, descriptor: int) -> None:
    is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
    real_close(descriptor)
    if is_directory:
        raise OSError("close failed")


def _is_content_stage(path, prefix: str) -> bool:
    name = str(path)
    return name.startswith(prefix) and not name.startswith(f"{prefix}mode-")


def _record_content_stage_modes(monkeypatch) -> list[tuple[str, int]]:
    observed = []
    real_open_stage = fs._open_stage

    class InspectingStage:
        def __init__(self, staged: str, handle) -> None:
            self.staged = staged
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def write(self, content: str) -> int:
            mode = stat.S_IMODE(os.fstat(self.handle.fileno()).st_mode)
            observed.append((self.staged, mode))
            return self.handle.write(content)

        def flush(self) -> None:
            self.handle.flush()

        def fileno(self) -> int:
            return self.handle.fileno()

        def __exit__(self, exc_type, exc, traceback):
            return self.handle.__exit__(exc_type, exc, traceback)

    def inspect(parent_fd, staged, flags, cleanup_reporter):
        return InspectingStage(
            staged,
            real_open_stage(parent_fd, staged, flags, cleanup_reporter),
        )

    monkeypatch.setattr(fs, "_open_stage", inspect)
    return observed


def test_new_chart_creates_exact_starter_content(tmp_path, capsys) -> None:
    chart_path = tmp_path / "nested" / "chart.yaml"

    assert main(["new", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Created {chart_path}\n"
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()


@pytest.mark.parametrize("entry_kind", ["file", "directory", "live-link", "dangling-link"])
def test_new_chart_non_force_refuses_every_existing_leaf(
    tmp_path, capsys, entry_kind
) -> None:
    chart_path = tmp_path / "chart.yaml"
    referent = tmp_path / "referent.yaml"
    if entry_kind == "file":
        chart_path.write_text("original\n", encoding="utf-8")
    elif entry_kind == "directory":
        chart_path.mkdir()
    elif entry_kind == "live-link":
        referent.write_text("referent\n", encoding="utf-8")
        chart_path.symlink_to(referent)
    else:
        chart_path.symlink_to(referent)

    assert main(["new", str(chart_path)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Refusing to overwrite existing file: {chart_path}\n"
    assert chart_path.is_symlink() is ("link" in entry_kind)
    if entry_kind == "file":
        assert chart_path.read_text(encoding="utf-8") == "original\n"
    if entry_kind == "live-link":
        assert referent.read_text(encoding="utf-8") == "referent\n"
    if entry_kind == "dangling-link":
        assert not referent.exists()


@pytest.mark.parametrize(
    "entry_kind", ["file", "directory", "live-link", "dangling-link", "fifo"]
)
def test_new_chart_non_force_refuses_existing_leaf_without_parent_write_access(
    tmp_path, capsys, entry_kind
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    chart_path = parent / "chart.yaml"
    referent = tmp_path / "referent.yaml"
    if entry_kind == "file":
        chart_path.write_text("original\n", encoding="utf-8")
    elif entry_kind == "directory":
        chart_path.mkdir()
    elif entry_kind == "live-link":
        referent.write_text("referent\n", encoding="utf-8")
        chart_path.symlink_to(referent)
    elif entry_kind == "dangling-link":
        chart_path.symlink_to(referent)
    else:
        os.mkfifo(chart_path)

    parent.chmod(0o500)
    try:
        assert main(["new", str(chart_path)]) == 2
    finally:
        parent.chmod(0o700)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Refusing to overwrite existing file: {chart_path}\n"
    assert list(parent.glob(".chordatlas-new-*.tmp")) == []
    if entry_kind == "file":
        assert chart_path.read_text(encoding="utf-8") == "original\n"
    if entry_kind == "live-link":
        assert referent.read_text(encoding="utf-8") == "referent\n"
    if entry_kind == "dangling-link":
        assert not referent.exists()


def test_new_chart_contains_parent_and_force_target_errors(tmp_path, capsys) -> None:
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory\n", encoding="utf-8")
    chart_path = blocked_parent / "chart.yaml"

    assert main(["new", str(chart_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to create chart:" in captured.err
    assert "Created" not in captured.err

    directory_target = tmp_path / "directory"
    directory_target.mkdir()
    assert main(["new", str(directory_target), "--force"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to create chart:" in captured.err
    assert "Created" not in captured.err


def test_new_chart_process_control_exception_propagates(tmp_path, monkeypatch) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt)

    with pytest.raises(KeyboardInterrupt):
        main(["new", str(tmp_path / "chart.yaml")])


def test_new_chart_race_winner_is_never_overwritten(tmp_path, capsys, monkeypatch) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_link = os.link

    def race_link(source, target, **kwargs):
        chart_path.write_text("race winner\n", encoding="utf-8")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", race_link)

    assert main(["new", str(chart_path)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Refusing to overwrite existing file" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "race winner\n"
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


@pytest.mark.parametrize("matching_stage", [False, True])
def test_new_chart_non_force_parent_retarget_stays_anchored(
    tmp_path, capsys, monkeypatch, matching_stage
) -> None:
    original_parent = tmp_path / "original"
    retargeted_parent = tmp_path / "retargeted"
    original_parent.mkdir()
    retargeted_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(original_parent, target_is_directory=True)
    chart_path = linked_parent / "chart.yaml"
    real_link = os.link

    def retarget_then_link(source, target, **kwargs):
        if matching_stage:
            (retargeted_parent / source).write_text("ATTACKER\n", encoding="utf-8")
        linked_parent.unlink()
        linked_parent.symlink_to(retargeted_parent, target_is_directory=True)
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", retarget_then_link)

    assert main(["new", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Created {chart_path}\n"
    assert (original_parent / "chart.yaml").read_text(encoding="utf-8") == example_song_yaml()
    assert not (retargeted_parent / "chart.yaml").exists()
    assert list(original_parent.glob(".chordatlas-new-*.tmp")) == []
    if matching_stage:
        attacker_stages = list(retargeted_parent.glob(".chordatlas-new-*.tmp"))
        assert len(attacker_stages) == 1
        assert attacker_stages[0].read_text(encoding="utf-8") == "ATTACKER\n"
    else:
        assert list(retargeted_parent.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_non_force_link_interrupt_propagates_and_cleans_stage(
    tmp_path, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "link", interrupt)

    with pytest.raises(KeyboardInterrupt):
        main(["new", str(chart_path)])

    assert not chart_path.exists()
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_non_force_reports_parent_descriptor_close_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)

    assert main(["new", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Chart created, but temporary cleanup failed:" in captured.err
    assert "unable to close destination directory: close failed" in captured.err
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()


@pytest.mark.parametrize("operation", ["non-force", "force", "metadata"])
def test_atomic_outputs_work_in_searchable_unreadable_parent(
    tmp_path, capsys, operation
) -> None:
    output_parent = tmp_path / "write-search-only"
    output_parent.mkdir()
    chart_path = tmp_path / "input.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    output_path = output_parent / ("metadata.json" if operation == "metadata" else "new.yaml")
    if operation in {"force", "metadata"}:
        output_path.write_text("ORIGINAL\n", encoding="utf-8")

    output_parent.chmod(0o300)
    try:
        probe = output_parent / "probe"
        try:
            descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except PermissionError:
            pytest.skip("filesystem does not permit write+search without directory read")
        else:
            os.close(descriptor)
            probe.unlink()
        try:
            list(output_parent.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("directory remains readable under the test permission mode")

        if operation == "metadata":
            result = main(
                [
                    "compare",
                    str(chart_path),
                    "--format",
                    "csv",
                    "--provenance",
                    "research",
                    "--metadata-json",
                    str(output_path),
                ]
            )
        else:
            command = ["new", str(output_path)]
            if operation == "force":
                command.append("--force")
            result = main(command)
    finally:
        output_parent.chmod(0o700)

    assert result == 0
    captured = capsys.readouterr()
    if operation == "metadata":
        assert captured.out.startswith("category,category_label,recording_id")
        assert '"metadata_schema_version": "1.0.0"' in output_path.read_text(
            encoding="utf-8"
        )
    else:
        assert captured.out == ""
        assert output_path.read_text(encoding="utf-8") == example_song_yaml()


def test_new_chart_staging_failure_never_publishes_target(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"

    def fail_content() -> str:
        raise OSError("content generation failed")

    monkeypatch.setattr("chordatlas.cli.example_song_yaml", fail_content)

    assert main(["new", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to create chart: content generation failed" in captured.err
    assert not chart_path.exists()
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_staging_name_exhaustion_is_operational_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    staged = tmp_path / ".chordatlas-new-fixed.tmp"
    staged.write_text("occupied\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    assert main(["new", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to create chart: unable to allocate a private staging file" in captured.err
    assert "Refusing to overwrite" not in captured.err
    assert not chart_path.exists()


def test_new_chart_reports_cleanup_failure_after_successful_publication(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_unlink = os.unlink

    def fail_staged_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-new-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_staged_unlink)

    assert main(["new", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    stages = list(tmp_path.glob(".chordatlas-new-*.tmp"))
    assert len(stages) == 1
    assert captured.err == (
        "Chart created, but temporary cleanup failed: "
        f"temporary file {stages[0].name}: cleanup denied\n"
    )
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert stages[0].read_text(encoding="utf-8") == example_song_yaml()
    assert stages[0].stat().st_ino == chart_path.stat().st_ino


def test_exclusive_publication_cleanup_failure_preserves_cause(
    tmp_path, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_unlink = os.unlink

    def fail_staged_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-new-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_staged_unlink)

    with pytest.raises(fs.PublishedCleanupError) as raised:
        fs.create_text_exclusive(
            chart_path,
            "PUBLISHED\n",
            stage_prefix=".chordatlas-new-",
        )

    stages = list(tmp_path.glob(".chordatlas-new-*.tmp"))
    assert len(stages) == 1
    assert stages[0].name in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    assert stages[0].stat().st_ino == chart_path.stat().st_ino


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(7)])
def test_exclusive_publication_cleanup_process_control_propagates(
    tmp_path, monkeypatch, control
) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_unlink = os.unlink

    def stop_staged_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-new-"):
            raise control
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", stop_staged_unlink)

    with pytest.raises(type(control)) as raised:
        fs.create_text_exclusive(
            chart_path,
            "PUBLISHED\n",
            stage_prefix=".chordatlas-new-",
        )

    assert raised.value is control
    stages = list(tmp_path.glob(".chordatlas-new-*.tmp"))
    assert len(stages) == 1
    assert stages[0].read_text(encoding="utf-8") == "PUBLISHED\n"
    assert chart_path.read_text(encoding="utf-8") == "PUBLISHED\n"
    assert stages[0].stat().st_ino == chart_path.stat().st_ino


def test_new_chart_supports_valid_long_leaf_name(tmp_path, capsys) -> None:
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    if name_max < 234:
        pytest.skip("filesystem component limit is below the regression threshold")
    chart_path = tmp_path / ("a" * min(240, name_max))

    assert main(["new", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Created {chart_path}\n"
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_staging_candidate_never_equals_target(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / ".chordatlas-new-fixed.tmp"
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    assert main(["new", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unable to allocate a private staging file" in captured.err
    assert not chart_path.exists()


def test_new_chart_force_replaces_regular_file_and_preserves_mode(tmp_path, capsys) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    chart_path.chmod(0o640)
    original_inode = chart_path.stat().st_ino

    assert main(["new", str(chart_path), "--force"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Created {chart_path}\n"
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert chart_path.stat().st_ino != original_inode
    assert chart_path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_force_preserves_supported_special_mode_bits(tmp_path, capsys) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    chart_path.chmod(0o4755)
    supported_mode = chart_path.stat().st_mode & 0o7777
    if supported_mode & 0o4000 == 0:
        pytest.skip("filesystem does not retain the set-user-ID bit")

    assert main(["new", str(chart_path), "--force"]) == 0

    capsys.readouterr()
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert chart_path.stat().st_mode & 0o7777 == supported_mode


def test_new_chart_force_new_file_uses_umask_mode(tmp_path, capsys) -> None:
    chart_path = tmp_path / "chart.yaml"
    previous_umask = os.umask(0o027)
    try:
        assert main(["new", str(chart_path), "--force"]) == 0
    finally:
        os.umask(previous_umask)

    capsys.readouterr()
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert chart_path.stat().st_mode & 0o777 == 0o640


def test_atomic_consumers_keep_content_stages_owner_only(
    tmp_path, capsys, monkeypatch
) -> None:
    observed = _record_content_stage_modes(monkeypatch)
    previous_umask = os.umask(0o000)
    try:
        chart_path = tmp_path / "chart.yaml"
        chart_path.write_text("ORIGINAL\n", encoding="utf-8")
        chart_path.chmod(0o644)
        assert main(["new", str(chart_path), "--force"]) == 0

        source = tmp_path / "source.yaml"
        metadata = tmp_path / "metadata.json"
        source.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
        assert (
            main(
                [
                    "compare",
                    str(source),
                    "--format",
                    "csv",
                    "--provenance",
                    "research",
                    "--metadata-json",
                    str(metadata),
                ]
            )
            == 0
        )

        sync_schema_mirror(tmp_path / "schemas")

        monkeypatch.setattr(
            snapshots,
            "_snapshot_files",
            lambda root, target: _stub_snapshot_files("first.txt"),
        )
        snapshots.regenerate_snapshots(tmp_path / "snapshot-workspace")
    finally:
        os.umask(previous_umask)

    capsys.readouterr()
    assert observed
    assert all(mode & 0o077 == 0 for _, mode in observed)
    assert chart_path.stat().st_mode & 0o777 == 0o644
    assert any(name.startswith(".chordatlas-new-") for name, _ in observed)
    assert any(name.startswith(".chordatlas-metadata-") for name, _ in observed)
    assert any(name.startswith(".chordatlas-schema-") for name, _ in observed)
    assert any(name.startswith(".chordatlas-snapshot-") for name, _ in observed)


def test_special_leaf_uses_private_stage_and_preserves_new_file_umask_mode(
    tmp_path, monkeypatch
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    destination = tmp_path / "special.txt"
    os.mkfifo(destination, 0o600)
    observed = _record_content_stage_modes(monkeypatch)
    previous_umask = os.umask(0o027)
    try:
        fs.replace_text(
            destination,
            "GENERATED\n",
            stage_prefix=".chordatlas-special-",
        )
    finally:
        os.umask(previous_umask)

    assert len(observed) == 1
    assert observed[0][1] == 0o600
    assert destination.is_file()
    assert destination.read_text(encoding="utf-8") == "GENERATED\n"
    assert destination.stat().st_mode & 0o777 == 0o640


def test_permission_probe_collision_exhaustion_preserves_existing_entry(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "target.txt"
    occupied = tmp_path / ".chordatlas-probe-mode-fixed.tmp"
    occupied.write_text("OCCUPIED\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    with pytest.raises(OSError, match="unable to allocate a private permission probe"):
        fs.replace_text(
            destination,
            "GENERATED\n",
            stage_prefix=".chordatlas-probe-",
        )

    assert not destination.exists()
    assert occupied.read_text(encoding="utf-8") == "OCCUPIED\n"


def test_permission_probe_cleanup_failure_aborts_before_content_staging(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "target.txt"
    real_unlink = os.unlink

    def fail_probe_unlink(path, *args, **kwargs):
        if str(path).startswith(".chordatlas-probe-mode-"):
            raise PermissionError("probe cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_probe_unlink)

    with pytest.raises(
        OSError,
        match=(
            r"Temporary cleanup failed: temporary file "
            r"\.chordatlas-probe-mode-.*: probe cleanup denied"
        ),
    ) as raised:
        fs.replace_text(
            destination,
            "GENERATED\n",
            stage_prefix=".chordatlas-probe-",
        )

    probes = list(tmp_path.glob(".chordatlas-probe-mode-*.tmp"))
    assert len(probes) == 1
    assert probes[0].read_bytes() == b""
    assert not destination.exists()
    assert [path for path in tmp_path.glob(".chordatlas-probe-*.tmp") if path not in probes] == []
    assert isinstance(raised.value.__cause__, PermissionError)


def test_new_chart_reports_retained_permission_probe(tmp_path, capsys, monkeypatch) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_unlink = os.unlink

    def fail_probe_unlink(path, *args, **kwargs):
        if str(path).startswith(".chordatlas-new-mode-"):
            raise PermissionError("probe cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_probe_unlink)

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    probes = list(tmp_path.glob(".chordatlas-new-mode-*.tmp"))
    assert len(probes) == 1
    assert probes[0].read_bytes() == b""
    assert not chart_path.exists()
    assert "Unable to create chart: Temporary cleanup failed:" in captured.err
    assert probes[0].name in captured.err
    assert "probe cleanup denied" in captured.err


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(7)])
def test_permission_probe_unlink_process_control_propagates(
    tmp_path, monkeypatch, control
) -> None:
    destination = tmp_path / "target.txt"
    real_unlink = os.unlink

    def stop_probe_unlink(path, *args, **kwargs):
        if str(path).startswith(".chordatlas-probe-mode-"):
            raise control
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", stop_probe_unlink)

    with pytest.raises(type(control)) as raised:
        fs.replace_text(
            destination,
            "GENERATED\n",
            stage_prefix=".chordatlas-probe-",
        )

    assert raised.value is control
    probes = list(tmp_path.glob(".chordatlas-probe-mode-*.tmp"))
    assert len(probes) == 1
    assert probes[0].read_bytes() == b""
    assert not destination.exists()


def test_permission_probe_process_control_propagates_and_cleans_probe(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "target.txt"
    monkeypatch.setattr(
        os,
        "fstat",
        lambda descriptor: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        fs.replace_text(
            destination,
            "GENERATED\n",
            stage_prefix=".chordatlas-probe-",
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".chordatlas-probe-*.tmp")) == []


def test_new_chart_force_generation_failure_preserves_existing_target(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    original_inode = chart_path.stat().st_ino

    def fail_content() -> str:
        raise OSError("content generation failed")

    monkeypatch.setattr("chordatlas.cli.example_song_yaml", fail_content)

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert "Unable to create chart: content generation failed" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert chart_path.stat().st_ino == original_inode
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["write", "close"])
def test_new_chart_force_stage_failure_preserves_existing_target(
    tmp_path, capsys, monkeypatch, failure_point
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    original_inode = chart_path.stat().st_ino

    class FailingStage:
        def __init__(self, staged: Path) -> None:
            self.staged = staged
            staged.write_text("", encoding="utf-8")

        def __enter__(self):
            return self

        def write(self, content: str) -> int:
            self.staged.write_text("PARTIAL", encoding="utf-8")
            if failure_point == "write":
                raise OSError("stage write failed")
            return len(content)

        def __exit__(self, exc_type, exc, traceback) -> None:
            if failure_point == "close":
                raise OSError("stage close failed")

    monkeypatch.setattr(
        fs,
        "_open_stage",
        lambda parent_fd, staged, flags, cleanup_reporter: FailingStage(tmp_path / staged),
    )

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert f"Unable to create chart: stage {failure_point} failed" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert chart_path.stat().st_ino == original_inode
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_force_staging_name_exhaustion_preserves_target(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    staged = tmp_path / ".chordatlas-new-fixed.tmp"
    staged.write_text("occupied\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert "unable to allocate a private staging file" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert staged.read_text(encoding="utf-8") == "occupied\n"


@pytest.mark.parametrize("link_kind", ["live", "dangling", "directory"])
def test_new_chart_force_replaces_leaf_symlink_without_touching_referent(
    tmp_path, capsys, link_kind
) -> None:
    chart_path = tmp_path / "chart.yaml"
    referent = tmp_path / "referent"
    if link_kind == "live":
        referent.write_text("referent\n", encoding="utf-8")
    elif link_kind == "directory":
        referent.mkdir()
    chart_path.symlink_to(referent, target_is_directory=link_kind == "directory")

    assert main(["new", str(chart_path), "--force"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Created {chart_path}\n"
    assert not chart_path.is_symlink()
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    if link_kind == "live":
        assert referent.read_text(encoding="utf-8") == "referent\n"
    elif link_kind == "directory":
        assert referent.is_dir()
    else:
        assert not referent.exists()


def test_new_chart_force_detaches_one_hard_link(tmp_path, capsys) -> None:
    peer = tmp_path / "peer.yaml"
    chart_path = tmp_path / "chart.yaml"
    peer.write_text("shared\n", encoding="utf-8")
    os.link(peer, chart_path)
    shared_inode = peer.stat().st_ino

    assert main(["new", str(chart_path), "--force"]) == 0

    capsys.readouterr()
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert peer.read_text(encoding="utf-8") == "shared\n"
    assert peer.stat().st_ino == shared_inode
    assert chart_path.stat().st_ino != shared_inode


def test_new_chart_force_preserves_parent_symlink_resolution(tmp_path, capsys) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(actual, target_is_directory=True)
    chart_path = linked_parent / "chart.yaml"

    assert main(["new", str(chart_path), "--force"]) == 0

    capsys.readouterr()
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()
    assert (actual / "chart.yaml").read_text(encoding="utf-8") == example_song_yaml()


@pytest.mark.parametrize("failure_point", ["chmod", "replace"])
def test_new_chart_force_failure_preserves_existing_target(
    tmp_path, capsys, monkeypatch, failure_point
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    chart_path.chmod(0o640)
    original_stat = chart_path.stat()

    if failure_point == "chmod":
        monkeypatch.setattr(
            os,
            "fchmod",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                PermissionError("chmod denied")
            ),
        )
    else:
        monkeypatch.setattr(
            os,
            "replace",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("replace failed")
            ),
        )

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to create chart:" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert chart_path.stat().st_ino == original_stat.st_ino
    assert chart_path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_force_reports_replace_and_cleanup_failures(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")
    real_unlink = os.unlink

    monkeypatch.setattr(
        os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    def fail_staged_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-new-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_staged_unlink)

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Temporary cleanup failed:" in captured.err
    assert "cleanup denied" in captured.err
    assert "Unable to create chart: replace failed" in captured.err
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert len(list(tmp_path.glob(".chordatlas-new-*.tmp"))) == 1


def test_new_chart_force_replace_interrupt_propagates_and_cleans_stage(
    tmp_path, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)

    with pytest.raises(KeyboardInterrupt):
        main(["new", str(chart_path), "--force"])

    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


def test_new_chart_force_reports_parent_descriptor_close_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)

    assert main(["new", str(chart_path), "--force"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Chart created, but temporary cleanup failed:" in captured.err
    assert "unable to close destination directory: close failed" in captured.err
    assert chart_path.read_text(encoding="utf-8") == example_song_yaml()


def test_new_chart_force_control_exception_survives_parent_close_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("original\n", encoding="utf-8")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "replace", interrupt)
    monkeypatch.setattr(os, "close", close_then_fail)

    with pytest.raises(KeyboardInterrupt) as raised:
        main(["new", str(chart_path), "--force"])

    captured = capsys.readouterr()
    assert "Temporary cleanup failed: close failed" in captured.err
    assert raised.value.__notes__ == ["Temporary cleanup failed: close failed"]
    assert chart_path.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".chordatlas-new-*.tmp")) == []


@pytest.mark.parametrize("matching_stage", [False, True])
def test_new_chart_force_parent_symlink_retarget_stays_anchored(
    tmp_path, capsys, monkeypatch, matching_stage
) -> None:
    original_parent = tmp_path / "original"
    retargeted_parent = tmp_path / "retargeted"
    original_parent.mkdir()
    retargeted_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(original_parent, target_is_directory=True)
    (original_parent / "chart.yaml").write_text("original\n", encoding="utf-8")
    (retargeted_parent / "chart.yaml").write_text("other\n", encoding="utf-8")
    chart_path = linked_parent / "chart.yaml"
    real_replace = os.replace

    def retarget_then_replace(source, target, **kwargs):
        if matching_stage:
            (retargeted_parent / source).write_text("ATTACKER\n", encoding="utf-8")
        linked_parent.unlink()
        linked_parent.symlink_to(retargeted_parent, target_is_directory=True)
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", retarget_then_replace)

    assert main(["new", str(chart_path), "--force"]) == 0

    captured = capsys.readouterr()
    assert captured.err == f"Created {chart_path}\n"
    assert (original_parent / "chart.yaml").read_text(encoding="utf-8") == example_song_yaml()
    assert (retargeted_parent / "chart.yaml").read_text(encoding="utf-8") == "other\n"
    assert list(original_parent.glob(".chordatlas-new-*.tmp")) == []
    if matching_stage:
        attacker_stages = list(retargeted_parent.glob(".chordatlas-new-*.tmp"))
        assert len(attacker_stages) == 1
        assert attacker_stages[0].read_text(encoding="utf-8") == "ATTACKER\n"
    else:
        assert list(retargeted_parent.glob(".chordatlas-new-*.tmp")) == []


def test_render_outputs_markdown(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "md"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("# Test\n")
    assert "## Chord Reference" in captured.out
    assert "### G" in captured.out
    assert captured.err == ""


def test_render_outputs_json_contract(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "json"]) == 0

    captured = capsys.readouterr()
    assert '"schema_version": "1.0.0"' in captured.out
    assert '"title": "Test"' in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("surface", ["render", "compare"])
@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("failure_kind", ["broken-pipe", "os-error"])
def test_render_and_compare_contain_stdout_failures(
    tmp_path, monkeypatch, surface, failure_point, failure_kind
) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\n", encoding="utf-8")
    neutralized = []
    stderr = io.StringIO()

    class FailingOutput:
        def write(self, content: str) -> int:
            if failure_point == "write":
                self._fail()
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                self._fail()

        def _fail(self) -> None:
            if failure_kind == "broken-pipe":
                raise BrokenPipeError("broken pipe")
            raise OSError("denied")

    monkeypatch.setattr(cli.sys, "stdout", FailingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli, "_neutralize_broken_stdout", lambda: neutralized.append("called")
    )
    command = [surface, str(chart_path), "--format", "json"]

    assert main(command) == 1

    if failure_kind == "broken-pipe":
        assert stderr.getvalue() == ""
        assert neutralized == ["called"]
    else:
        assert stderr.getvalue() == "Unable to write output: denied\n"
        assert neutralized == []


@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_stdout_process_control_exceptions_propagate(
    monkeypatch, failure_point, interrupt
) -> None:
    class InterruptingOutput:
        def write(self, content: str) -> int:
            if failure_point == "write":
                raise interrupt
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                raise interrupt

    neutralized = []
    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", InterruptingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli, "_neutralize_broken_stdout", lambda: neutralized.append("called")
    )

    with pytest.raises(interrupt):
        cli._write_stdout("output")

    assert stderr.getvalue() == ""
    assert neutralized == []


def test_stdout_success_preserves_exact_content_and_flushes(monkeypatch) -> None:
    class RecordingOutput:
        def __init__(self) -> None:
            self.content = ""
            self.flushes = 0

        def write(self, content: str) -> int:
            self.content += content
            return len(content)

        def flush(self) -> None:
            self.flushes += 1

    output = RecordingOutput()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli._write_stdout("exact output\n") == 0
    assert output.content == "exact output\n"
    assert output.flushes == 1


def test_broken_stdout_neutralization_keeps_reused_stdout_descriptor_open(
    monkeypatch,
) -> None:
    closed = []

    class BrokenOutput:
        def fileno(self) -> int:
            return 17

    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, "stdout", BrokenOutput())
        patch.setattr(os, "open", lambda path, flags: 17)
        patch.setattr(os, "close", lambda descriptor: closed.append(descriptor))
        cli._neutralize_broken_stdout()

    assert closed == []


def test_broken_stdout_neutralization_falls_back_without_masking_cleanup_failures(
    monkeypatch,
) -> None:
    fallback = io.StringIO()

    class BrokenOutput:
        def fileno(self) -> int:
            return 17

    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, "stdout", BrokenOutput())
        patch.setattr(os, "open", lambda path, flags: 18)
        patch.setattr(
            os,
            "dup2",
            lambda source, target: (_ for _ in ()).throw(OSError("dup failed")),
        )
        patch.setattr(
            os,
            "close",
            lambda descriptor: (_ for _ in ()).throw(OSError("close failed")),
        )
        patch.setattr(builtins, "open", lambda *args, **kwargs: fallback)
        cli._neutralize_broken_stdout()
        assert cli.sys.stdout is fallback


def test_render_subprocess_contains_real_broken_pipe(tmp_path) -> None:
    chart_path = tmp_path / "pipe.yaml"
    chart_path.write_text(
        "title: Pipe\nsections: []\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "chordatlas.cli",
                "render",
                str(chart_path),
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            stdout=write_fd,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_fd)

    with process:
        assert process.stderr is not None
        assert process.wait(timeout=10) == 1
        stderr = process.stderr.read().decode("utf-8")
        assert "BrokenPipeError" not in stderr
        assert "Exception ignored" not in stderr
        assert "Traceback" not in stderr


def test_cli_parser_preserves_healthy_root_help_bytes_and_exit(capsys) -> None:
    expected = cli._build_parser().format_help()

    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.out == expected
    assert captured.err == ""


def test_cli_parser_preserves_healthy_subcommand_help_bytes_and_exit(capsys) -> None:
    parser = cli._build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if "snapshots" in (getattr(action, "choices", None) or {})
    )
    expected = subparsers.choices["snapshots"].format_help()

    with pytest.raises(SystemExit) as raised:
        main(["snapshots", "--help"])

    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.out == expected
    assert captured.err == ""


def test_cli_parser_preserves_healthy_no_command_help_and_exit(capsys) -> None:
    expected = cli._build_parser().format_help()

    assert main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""


def test_cli_subparsers_inherit_stdout_safe_parser() -> None:
    parser = cli._build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if "snapshots" in (getattr(action, "choices", None) or {})
    )

    assert all(isinstance(child, cli._CliParser) for child in subparsers.choices.values())


@pytest.mark.parametrize("surface", ["root-help", "subcommand-help", "no-command"])
@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("failure_kind", ["broken-pipe", "os-error"])
def test_cli_parser_contains_stdout_failures(
    monkeypatch, surface, failure_point, failure_kind
) -> None:
    neutralized = []
    stderr = io.StringIO()

    class FailingOutput:
        def write(self, content: str) -> int:
            if failure_point == "write":
                self._fail()
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                self._fail()

        def _fail(self) -> None:
            if failure_kind == "broken-pipe":
                raise BrokenPipeError("broken pipe")
            raise OSError("denied")

    monkeypatch.setattr(cli.sys, "stdout", FailingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli, "_neutralize_broken_stdout", lambda: neutralized.append("called")
    )
    argv = {
        "root-help": ["--help"],
        "subcommand-help": ["snapshots", "--help"],
        "no-command": [],
    }[surface]

    if surface == "no-command":
        assert main(argv) == 1
    else:
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 1

    if failure_kind == "broken-pipe":
        assert stderr.getvalue() == ""
        assert neutralized == ["called"]
    else:
        assert stderr.getvalue() == "Unable to write output: denied\n"
        assert neutralized == []


@pytest.mark.parametrize("surface", ["root-help", "subcommand-help", "no-command"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_cli_parser_output_process_control_propagates(
    monkeypatch, surface, interrupt
) -> None:
    class InterruptingOutput:
        def write(self, content: str) -> int:
            raise interrupt(9) if interrupt is SystemExit else interrupt()

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    monkeypatch.setattr(cli.sys, "stdout", InterruptingOutput())
    argv = {
        "root-help": ["--help"],
        "subcommand-help": ["snapshots", "--help"],
        "no-command": [],
    }[surface]

    with pytest.raises(interrupt) as raised:
        main(argv)
    if interrupt is SystemExit:
        assert raised.value.code == 9


def test_cli_parser_preserves_usage_error_stderr_and_exit(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_write_stdout",
        lambda content: (_ for _ in ()).throw(AssertionError("unexpected stdout")),
    )

    with pytest.raises(SystemExit) as raised:
        main(["render", "song.yaml", "--unknown"])

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert "usage: chordchart [-h]" in captured.err
    assert "unrecognized arguments: --unknown" in captured.err


def test_cli_parser_preserves_custom_help_stream(monkeypatch) -> None:
    parser = cli._build_parser()
    output = io.StringIO()
    monkeypatch.setattr(
        cli,
        "_write_stdout",
        lambda content: (_ for _ in ()).throw(AssertionError("unexpected stdout")),
    )

    parser.print_help(file=output)

    assert output.getvalue() == parser.format_help()


@pytest.mark.parametrize(
    "command",
    [[], ["--help"], ["snapshots", "--help"]],
    ids=["no-command", "root-help", "subcommand-help"],
)
def test_cli_parser_subprocess_contains_closed_stdout(command) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    process = subprocess.Popen(
        [sys.executable, "-m", "chordatlas.cli", *command],
        cwd=ROOT,
        env=env,
        stdout=write_fd,
        stderr=subprocess.PIPE,
    )
    os.close(write_fd)
    assert process.stderr is not None

    assert process.wait(timeout=10) == 1
    assert process.stderr.read() == b""


@pytest.mark.parametrize("surface", ["parser-error", "application-error", "success"])
@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("failure_kind", ["broken-pipe", "os-error"])
def test_cli_contains_stderr_failures(
    monkeypatch, surface, failure_point, failure_kind
) -> None:
    class FailingError:
        def write(self, content: str) -> int:
            if failure_point == "write":
                self._fail()
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                self._fail()

        def fileno(self) -> int:
            return 17

        def _fail(self) -> None:
            if failure_kind == "broken-pipe":
                raise BrokenPipeError("broken pipe")
            raise OSError("denied")

    monkeypatch.setattr(cli.sys, "stderr", FailingError())
    command = {
        "parser-error": ["--definitely-invalid"],
        "application-error": ["render", "/definitely/missing"],
        "success": ["schemas", "--check"],
    }[surface]

    if surface == "parser-error":
        with pytest.raises(SystemExit) as raised:
            main(command)
        assert raised.value.code == 1
    else:
        assert main(command) == 1

    assert isinstance(cli.sys.stderr, cli._ManagedStderr)
    assert cli.sys.stderr.failed


@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_stderr_process_control_exceptions_propagate(
    monkeypatch, failure_point, interrupt
) -> None:
    class InterruptingError:
        def write(self, content: str) -> int:
            if failure_point == "write":
                self._stop()
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                self._stop()

        def _stop(self) -> None:
            raise interrupt(9) if interrupt is SystemExit else interrupt()

    original = InterruptingError()
    monkeypatch.setattr(cli.sys, "stderr", original)

    with pytest.raises(interrupt) as raised:
        main(["render", "/definitely/missing"])
    if interrupt is SystemExit:
        assert raised.value.code == 9
    assert cli.sys.stderr is original


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_application_process_control_survives_failed_stderr_flush(
    monkeypatch, interrupt
) -> None:
    class BrokenFlush:
        def write(self, content: str) -> int:
            return len(content)

        def flush(self) -> None:
            raise BrokenPipeError("broken pipe")

        def fileno(self) -> int:
            return 17

    monkeypatch.setattr(cli.sys, "stderr", BrokenFlush())

    def stop(argv):
        raise interrupt(9) if interrupt is SystemExit else interrupt()

    monkeypatch.setattr(cli, "_main", stop)

    with pytest.raises(interrupt) as raised:
        main([])
    if interrupt is SystemExit:
        assert raised.value.code == 9
    assert isinstance(cli.sys.stderr, cli._ManagedStderr)
    assert cli.sys.stderr.failed


def test_healthy_application_process_control_restores_original_stderr(
    monkeypatch,
) -> None:
    original = io.StringIO()
    monkeypatch.setattr(cli.sys, "stderr", original)
    monkeypatch.setattr(
        cli,
        "_main",
        lambda argv: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        main([])

    assert cli.sys.stderr is original


def test_managed_stderr_does_not_clobber_custom_descriptor(monkeypatch) -> None:
    fallback = io.StringIO()
    duplicated = []

    class CustomError:
        def write(self, content: str) -> int:
            raise OSError("denied")

        def flush(self) -> None:
            pass

        def fileno(self) -> int:
            return 17

    managed = cli._ManagedStderr(CustomError())
    with monkeypatch.context() as patch:
        patch.setattr(os, "dup2", lambda source, target: duplicated.append((source, target)))
        patch.setattr(builtins, "open", lambda *args, **kwargs: fallback)
        assert managed.write("diagnostic") == len("diagnostic")

    assert managed.failed
    assert managed._stream is fallback
    assert duplicated == []


@pytest.mark.parametrize("devnull_fd", [2, 18])
def test_managed_stderr_redirects_only_process_stderr_and_closes_auxiliary_fd(
    monkeypatch, devnull_fd
) -> None:
    duplicated = []
    closed = []

    class ProcessError:
        def write(self, content: str) -> int:
            raise BrokenPipeError("broken pipe")

        def flush(self) -> None:
            pass

        def fileno(self) -> int:
            return 2

    managed = cli._ManagedStderr(ProcessError())
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda path, flags: devnull_fd)
        patch.setattr(os, "dup2", lambda source, target: duplicated.append((source, target)))
        patch.setattr(os, "close", lambda descriptor: closed.append(descriptor))
        assert managed.write("diagnostic") == len("diagnostic")

    assert managed.failed
    if devnull_fd == 2:
        assert duplicated == []
        assert closed == []
    else:
        assert duplicated == [(18, 2)]
        assert closed == [18]


def test_managed_stderr_fallback_does_not_mask_redirect_cleanup_failures(
    monkeypatch,
) -> None:
    fallback = io.StringIO()

    class ProcessError:
        def write(self, content: str) -> int:
            raise BrokenPipeError("broken pipe")

        def flush(self) -> None:
            pass

        def fileno(self) -> int:
            return 2

    managed = cli._ManagedStderr(ProcessError())
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda path, flags: 18)
        patch.setattr(
            os,
            "dup2",
            lambda source, target: (_ for _ in ()).throw(OSError("dup failed")),
        )
        patch.setattr(
            os,
            "close",
            lambda descriptor: (_ for _ in ()).throw(OSError("close failed")),
        )
        patch.setattr(builtins, "open", lambda *args, **kwargs: fallback)
        assert managed.write("diagnostic") == len("diagnostic")

    assert managed.failed
    assert managed._stream is fallback


def test_release_check_receives_managed_stderr(monkeypatch) -> None:
    original = io.StringIO()
    received = []

    def fake_release_check(*, stderr) -> int:
        received.append(stderr)
        print("release diagnostic", file=stderr)
        return 0

    monkeypatch.setattr(cli.sys, "stderr", original)
    monkeypatch.setattr(cli, "run_release_check", fake_release_check)

    assert main(["release-check"]) == 0

    assert len(received) == 1
    assert isinstance(received[0], cli._ManagedStderr)
    assert original.getvalue() == "release diagnostic\n"
    assert cli.sys.stderr is original


@pytest.mark.parametrize(
    "command",
    [
        ["--definitely-invalid"],
        ["render", "/definitely/missing"],
        ["schemas", "--check"],
        ["snapshots", "check"],
    ],
    ids=["parser-error", "application-error", "schema-success", "snapshot-success"],
)
def test_cli_subprocess_contains_closed_stderr(command) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    process = subprocess.Popen(
        [sys.executable, "-m", "chordatlas.cli", *command],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=write_fd,
    )
    os.close(write_fd)
    assert process.stdout is not None

    assert process.wait(timeout=10) == 1
    assert process.stdout.read() == b""


def test_render_scalar_string_sequences_match_one_item_lists(tmp_path, capsys) -> None:
    scalar_path = tmp_path / "scalar.yaml"
    list_path = tmp_path / "list.yaml"
    scalar_path.write_text(
        "\n".join(
            [
                "title: Test",
                "voicing_notes: Open position",
                "performance_notes: Palm mute",
                "recording_notes: Double tracked",
                "sections:",
                "  - name: Verse",
                "    notes: Build gradually",
                "    bars:",
                "      - Am",
                "      - chords: G",
                "",
            ]
        ),
        encoding="utf-8",
    )
    list_path.write_text(
        "\n".join(
            [
                "title: Test",
                "voicing_notes: [Open position]",
                "performance_notes: [Palm mute]",
                "recording_notes: [Double tracked]",
                "sections:",
                "  - name: Verse",
                "    notes: [Build gradually]",
                "    bars:",
                "      - [Am]",
                "      - chords: [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(scalar_path), "--format", "json"]) == 0
    scalar_payload = json.loads(capsys.readouterr().out)
    assert main(["render", str(list_path), "--format", "json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)

    assert scalar_payload == list_payload
    assert scalar_payload["sections"][0]["bars"] == [["Am"], ["G"]]


def test_render_normalizes_optional_schema_strings(tmp_path, capsys) -> None:
    chart_path = tmp_path / "scalar-metadata.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "artist: false",
                "key: 0",
                "source: false",
                "sections:",
                "  - name: Intro",
                "    repeat: 0",
                "    bars:",
                "      - chords: [G]",
                "        timestamp: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["artist"] == "False"
    assert payload["key"] == "0"
    assert payload["source"] == "False"
    assert payload["sections"][0]["repeat"] == "0"
    assert payload["sections"][0]["bars"][0]["timestamp"] == "False"

    assert main(["validate", str(chart_path)]) == 0
    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped" not in captured.err
    assert captured.out == ""


def test_render_normalizes_known_analysis_scalar_lists(tmp_path, capsys) -> None:
    chart_path = tmp_path / "analysis-scalars.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "analysis:",
                "  roman: I",
                "  nashville: 1",
                "  custom:",
                "    nested: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["analysis"] == {
        "roman": ["I"],
        "nashville": [1],
        "custom": {"nested": True},
    }

    assert main(["validate", str(chart_path)]) == 0
    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "analysis_yaml",
    [
        "  roman: null",
        "  roman: {I: 1}",
        "  roman: [[I]]",
        "  roman: [I, null]",
    ],
)
def test_chart_commands_reject_invalid_known_analysis_values(
    tmp_path,
    capsys,
    analysis_yaml: str,
) -> None:
    chart_path = tmp_path / "invalid-analysis.yaml"
    chart_path.write_text(f"title: Test\nanalysis:\n{analysis_yaml}\n", encoding="utf-8")

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart: analysis.roman" in captured.err
        assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("analysis_yaml", "message"),
    [
        ("  roman: [.nan]", "analysis.roman[0] must be finite"),
        (
            "  custom:\n    - value: .inf",
            "analysis.custom[0].value must be finite",
        ),
        (
            '  custom: !!binary "SGVsbG8="',
            "analysis.custom must contain only JSON-compatible values",
        ),
        (
            "  custom: 2026-07-22",
            "analysis.custom must contain only JSON-compatible values",
        ),
        ('  "a.b": .nan', 'analysis["a.b"] must be finite'),
        ("  1: value", "analysis keys must be strings"),
    ],
)
def test_chart_commands_reject_non_json_analysis_values(
    tmp_path,
    capsys,
    analysis_yaml: str,
    message: str,
) -> None:
    chart_path = tmp_path / "non-json-analysis.yaml"
    chart_path.write_text(f"title: Test\nanalysis:\n{analysis_yaml}\n", encoding="utf-8")

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Invalid chart: {message}" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_reject_recursive_yaml_analysis_aliases(tmp_path, capsys) -> None:
    chart_path = tmp_path / "recursive-analysis.yaml"
    chart_path.write_text(
        "title: Test\nanalysis: &analysis\n  custom: *analysis\n",
        encoding="utf-8",
    )

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart: analysis.custom contains a recursive container" in captured.err
        assert "Traceback" not in captured.err


def test_render_reports_malformed_yaml(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["render", str(chart_path), "--format", "md"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid chart: Expected YAML mapping" in captured.err


def test_chart_commands_contain_yaml_syntax_errors(tmp_path, capsys) -> None:
    chart_path = tmp_path / "syntax-error.yaml"
    chart_path.write_text("title: [\n", encoding="utf-8")

    for command in (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    ):
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Invalid chart: Invalid YAML in {chart_path}" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_reject_duplicate_yaml_keys(tmp_path, capsys) -> None:
    chart_path = tmp_path / "duplicate.yaml"
    chart_path.write_text("title: First\ntitle: Second\n", encoding="utf-8")

    for command in (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    ):
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Invalid chart: Duplicate YAML key 'title' in {chart_path} at line 2" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_reject_provenance_key_normalization_collisions(
    tmp_path, capsys
) -> None:
    chart_path = tmp_path / "provenance-collision.yaml"
    chart_path.write_text(
        "title: Test\n"
        "metadata_provenance:\n"
        "  1: {source_type: audio}\n"
        "  '1': {source_type: contributor}\n",
        encoding="utf-8",
    )

    for command in (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    ):
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "Invalid chart: metadata_provenance keys 1 and '1' both normalize to '1'"
            in captured.err
        )
        assert "Traceback" not in captured.err


def test_chart_commands_reject_compact_identity_key_collisions(tmp_path, capsys) -> None:
    chart_path = tmp_path / "identity-collision.yaml"
    chart_path.write_text(
        "title: Test\n"
        "structured_recording_notes:\n"
        "  1: [First]\n"
        "  '1': [Second]\n",
        encoding="utf-8",
    )

    for command in (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    ):
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "Invalid chart: structured_recording_notes keys 1 and '1' both normalize to '1'"
            in captured.err
        )
        assert "Traceback" not in captured.err


def test_chart_commands_contain_deep_yaml_input(tmp_path, capsys) -> None:
    chart_path = tmp_path / "deep.yaml"
    lines = ["title: Test", "analysis:", "  custom:"]
    lines.extend(f"{'  ' * depth}next:" for depth in range(2, 302))
    lines.append(f"{'  ' * 302}value: 1")
    chart_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for command in (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    ):
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart:" in captured.err
        assert "nesting depth" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_report_empty_title_without_traceback(tmp_path, capsys) -> None:
    chart_path = tmp_path / "empty-title.yaml"
    chart_path.write_text("title: ''\nsections: []\n", encoding="utf-8")

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart: SongChart.title must not be empty" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_report_missing_title_without_traceback(tmp_path, capsys) -> None:
    chart_path = tmp_path / "missing-title.yaml"
    chart_path.write_text("sections: malformed sibling\n", encoding="utf-8")

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart: Song charts must include a title" in captured.err
        assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("chart_yaml", "message"),
    [
        (
            "\n".join(
                [
                    "title: Test",
                    "recordings:",
                    "  studio:",
                    "    id: remaster",
                    "    provenance: malformed",
                    "",
                ]
            ),
            (
                "recordings mapping key 'studio' conflicts with nested id 'remaster'; "
                "remove the nested id or use the explicit list form"
            ),
        ),
        (
            "\n".join(
                [
                    "title: Test",
                    "recordings:",
                    "  studio: Studio recording",
                    "structured_recording_notes:",
                    "  Effects:",
                    "    name: Tuning",
                    "    notes: malformed",
                    "    recording_ids: [missing]",
                    "",
                ]
            ),
            (
                "structured_recording_notes mapping key 'Effects' conflicts with nested "
                "name 'Tuning'; remove the nested name or use the explicit list form"
            ),
        ),
    ],
)
def test_chart_commands_reject_conflicting_compact_identities(
    tmp_path,
    capsys,
    chart_yaml: str,
    message: str,
) -> None:
    chart_path = tmp_path / "identity-conflict.yaml"
    chart_path.write_text(chart_yaml, encoding="utf-8")

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Invalid chart: {message}" in captured.err
        assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("scope_yaml", "path"),
    [
        (
            "    recording_ids: [studio]\n    recording_id: live\n"
            "    notes: [Light chorus]",
            "structured_recording_notes['Effects']",
        ),
        (
            "    notes:\n      - text: Light chorus\n"
            "        recording_ids: [studio]\n        recording_id: live",
            "structured_recording_notes['Effects'].notes[0]",
        ),
        (
            "    value:\n      text: Light chorus\n"
            "      recording_ids: [studio]\n      recording_id: live",
            "structured_recording_notes['Effects'].value",
        ),
    ],
)
def test_chart_commands_reject_conflicting_recording_scope_aliases(
    tmp_path,
    capsys,
    scope_yaml: str,
    path: str,
) -> None:
    chart_path = tmp_path / "recording-scope-conflict.yaml"
    chart_path.write_text(
        "title: Test\nrecordings:\n  studio: Studio\n  live: Live\n"
        f"structured_recording_notes:\n  Effects:\n{scope_yaml}\n",
        encoding="utf-8",
    )
    message = (
        f"{path} has conflicting recording scope aliases: "
        "recording_ids=['studio'], recording_id='live'; keep only recording_ids"
    )

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"Invalid chart: {message}" in captured.err
        assert "Traceback" not in captured.err


def test_chart_commands_report_invalid_bars_container_without_traceback(tmp_path, capsys) -> None:
    chart_path = tmp_path / "invalid-bars.yaml"
    chart_path.write_text(
        "title: Test\nsections:\n  - name: Intro\n    bars: 1\n",
        encoding="utf-8",
    )

    commands = (
        ["render", str(chart_path), "--format", "json"],
        ["compare", str(chart_path), "--format", "json"],
        ["validate", str(chart_path)],
    )
    for command in commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid chart: Chart section bars must be a list" in captured.err
        assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("title: Test\nrecordings: 0\n", "recordings must be a list or mapping"),
        (
            "title: Test\nstructured_recording_notes: false\n",
            "structured_recording_notes must be a list or mapping",
        ),
        (
            "title: Test\nrecordings:\n  studio:\n    notes: 0\n",
            "recording source notes must be a list",
        ),
        (
            "title: Test\nstructured_recording_notes:\n"
            "  Effects:\n    notes: false\n    value: Valid claim\n",
            "recording note group notes must be a string, mapping, or list",
        ),
    ],
)
def test_render_reports_wrong_falsey_recording_containers(
    tmp_path,
    capsys,
    body: str,
    message: str,
) -> None:
    chart_path = tmp_path / "wrong-falsey.yaml"
    chart_path.write_text(body, encoding="utf-8")

    assert main(["render", str(chart_path), "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Invalid chart: {message}" in captured.err
    assert "Traceback" not in captured.err


def test_compare_outputs_json_contract(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json"]) == 0

    captured = capsys.readouterr()
    assert '"comparison_schema_version": "1.0.0"' in captured.out
    assert '"source_specific_claim_count": 2' in captured.out
    assert captured.err == ""


def test_compare_outputs_markdown(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "md"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("# Recording Comparison: Test\n")
    assert "## Summary" in captured.out
    assert "## Effects" in captured.out
    assert captured.err == ""


def test_compare_outputs_text(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "txt"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("Recording Comparison: Test\n")
    assert "Summary" in captured.out
    assert "EFFECTS" in captured.out
    assert captured.err == ""


def test_compare_outputs_filtered_csv(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--category",
                "effects",
                "--recording",
                "studio",
                "--severity",
                "medium",
                "--source-specific-only",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific"
    )
    assert (
        "effects,Effects,studio,Studio recording,Effects,Light chorus,medium,true"
        in captured.out
    )
    assert "Dryer amp tone" not in captured.out
    assert captured.err == ""


def test_compare_outputs_provenance_aware_json(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json", "--provenance", "research"]) == 0

    captured = capsys.readouterr()
    assert '"claim_origin": "inferred"' in captured.out
    assert '"evidence_refs": [' in captured.out
    assert '"source_url": "https://example.invalid/studio"' in captured.out
    assert captured.err == ""


def test_compare_outputs_extended_csv_when_provenance_is_requested(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "csv", "--provenance", "standard"]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific,confidence,claim_origin,provenance_summary,"
        "evidence_refs,recording_source_url,recording_version_label"
    )
    assert "medium,inferred," in captured.out
    assert "https://example.invalid/studio,Studio reference" in captured.out
    assert captured.err == ""


def test_compare_writes_metadata_json_sidecar_for_research_csv(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "comparison.metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("category,category_label,recording_id")
    assert captured.err == ""
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"metadata_schema_version": "1.0.0"' in metadata
    assert '"ref_id": "studio-00-00"' in metadata
    assert '"source_url": "https://example.invalid/studio"' in metadata


def test_compare_stdout_failure_preserves_completed_metadata_sidecar(
    tmp_path, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "comparison.metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    stderr = io.StringIO()

    class FailingOutput:
        def write(self, content: str) -> int:
            raise OSError("denied")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    monkeypatch.setattr(cli.sys, "stdout", FailingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    assert stderr.getvalue() == "Unable to write output: denied\n"
    assert '"metadata_schema_version": "1.0.0"' in metadata_path.read_text(
        encoding="utf-8"
    )
    assert list(tmp_path.glob(".chordatlas-metadata-*.tmp")) == []


def test_compare_metadata_replaces_regular_file_and_preserves_supported_mode(
    tmp_path, capsys
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    metadata_path.write_text("ORIGINAL\n", encoding="utf-8")
    metadata_path.chmod(0o4755)
    supported_mode = metadata_path.stat().st_mode & 0o7777
    original_inode = metadata_path.stat().st_ino

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("category,category_label,recording_id")
    assert captured.err == ""
    assert metadata_path.stat().st_ino != original_inode
    assert metadata_path.stat().st_mode & 0o7777 == supported_mode
    assert '"metadata_schema_version": "1.0.0"' in metadata_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("link_kind", ["live", "dangling", "directory"])
def test_compare_metadata_replaces_leaf_symlink_without_touching_referent(
    tmp_path, capsys, link_kind
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    referent = tmp_path / "referent"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    if link_kind == "live":
        referent.write_text("REFERENT\n", encoding="utf-8")
    elif link_kind == "directory":
        referent.mkdir()
    metadata_path.symlink_to(referent, target_is_directory=link_kind == "directory")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("category,category_label,recording_id")
    assert captured.err == ""
    assert not metadata_path.is_symlink()
    assert '"metadata_schema_version": "1.0.0"' in metadata_path.read_text(
        encoding="utf-8"
    )
    if link_kind == "live":
        assert referent.read_text(encoding="utf-8") == "REFERENT\n"
    elif link_kind == "directory":
        assert referent.is_dir()
    else:
        assert not referent.exists()


def test_compare_metadata_detaches_one_hard_link(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    peer = tmp_path / "peer.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    peer.write_text("SHARED\n", encoding="utf-8")
    os.link(peer, metadata_path)
    shared_inode = peer.stat().st_ino

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    capsys.readouterr()
    assert peer.read_text(encoding="utf-8") == "SHARED\n"
    assert peer.stat().st_ino == shared_inode
    assert metadata_path.stat().st_ino != shared_inode


def test_compare_metadata_generation_alias_race_preserves_input(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    original = _provenance_comparison_chart_yaml()
    chart_path.write_text(original, encoding="utf-8")
    real_metadata = cli.comparison_metadata_to_json

    def create_alias(*args, **kwargs):
        os.link(chart_path, metadata_path)
        return real_metadata(*args, **kwargs)

    monkeypatch.setattr(cli, "comparison_metadata_to_json", create_alias)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "--metadata-json must not overwrite the input chart\n"
    assert chart_path.read_text(encoding="utf-8") == original
    assert metadata_path.samefile(chart_path)


def test_compare_metadata_pinned_guard_rejects_parent_retarget_to_input(
    tmp_path, capsys, monkeypatch
) -> None:
    input_parent = tmp_path / "input"
    safe_parent = tmp_path / "safe"
    input_parent.mkdir()
    safe_parent.mkdir()
    chart_path = input_parent / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(safe_parent, target_is_directory=True)
    metadata_path = linked_parent / "song.yaml"
    original = chart_path.read_text(encoding="utf-8")
    real_mkdir = Path.mkdir

    def retarget_before_open(path, *args, **kwargs):
        if path == linked_parent:
            linked_parent.unlink()
            linked_parent.symlink_to(input_parent, target_is_directory=True)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", retarget_before_open)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "--metadata-json must not overwrite the input chart\n"
    assert chart_path.read_text(encoding="utf-8") == original
    assert list(input_parent.glob(".chordatlas-metadata-*.tmp")) == []


@pytest.mark.parametrize("matching_stage", [False, True])
def test_compare_metadata_parent_retarget_stays_anchored(
    tmp_path, capsys, monkeypatch, matching_stage
) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    original_parent = tmp_path / "original"
    retargeted_parent = tmp_path / "retargeted"
    original_parent.mkdir()
    retargeted_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(original_parent, target_is_directory=True)
    metadata_path = linked_parent / "metadata.json"
    (retargeted_parent / "metadata.json").write_text("OTHER\n", encoding="utf-8")
    real_stat = os.stat
    retargeted = False

    def retarget_after_guard(path, *args, **kwargs):
        nonlocal retargeted
        try:
            return real_stat(path, *args, **kwargs)
        except FileNotFoundError:
            if kwargs.get("dir_fd") is not None and kwargs.get("follow_symlinks") is True:
                if matching_stage:
                    (retargeted_parent / ".chordatlas-metadata-fixed.tmp").write_text(
                        "ATTACKER\n", encoding="utf-8"
                    )
                linked_parent.unlink()
                linked_parent.symlink_to(retargeted_parent, target_is_directory=True)
                retargeted = True
            raise

    monkeypatch.setattr(os, "stat", retarget_after_guard)
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert retargeted
    assert captured.out.startswith("category,category_label,recording_id")
    assert '"metadata_schema_version": "1.0.0"' in (
        original_parent / "metadata.json"
    ).read_text(encoding="utf-8")
    assert (retargeted_parent / "metadata.json").read_text(encoding="utf-8") == "OTHER\n"
    assert list(original_parent.glob(".chordatlas-metadata-*.tmp")) == []
    if matching_stage:
        attacker_stage = retargeted_parent / ".chordatlas-metadata-fixed.tmp"
        assert attacker_stage.read_text(encoding="utf-8") == "ATTACKER\n"


def test_compare_metadata_replace_failure_preserves_existing_sidecar(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    metadata_path.write_text("ORIGINAL\n", encoding="utf-8")
    original_inode = metadata_path.stat().st_ino
    monkeypatch.setattr(
        os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to write metadata JSON: replace failed" in captured.err
    assert metadata_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert metadata_path.stat().st_ino == original_inode
    assert list(tmp_path.glob(".chordatlas-metadata-*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["write", "close"])
def test_compare_metadata_stage_failure_preserves_existing_sidecar(
    tmp_path, capsys, monkeypatch, failure_point
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    metadata_path.write_text("ORIGINAL\n", encoding="utf-8")
    original_inode = metadata_path.stat().st_ino

    class FailingStage:
        def __init__(self, staged: Path) -> None:
            self.staged = staged
            staged.write_text("", encoding="utf-8")

        def __enter__(self):
            return self

        def write(self, content: str) -> int:
            self.staged.write_text("PARTIAL", encoding="utf-8")
            if failure_point == "write":
                raise OSError("stage write failed")
            return len(content)

        def flush(self) -> None:
            pass

        def fileno(self) -> int:
            return -1

        def __exit__(self, exc_type, exc, traceback) -> None:
            if failure_point == "close":
                raise OSError("stage close failed")

    monkeypatch.setattr(
        fs,
        "_open_stage",
        lambda parent_fd, staged, flags, cleanup_reporter: FailingStage(tmp_path / staged),
    )
    monkeypatch.setattr(os, "fchmod", lambda descriptor, mode: None)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Unable to write metadata JSON: stage {failure_point} failed" in captured.err
    assert metadata_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert metadata_path.stat().st_ino == original_inode
    assert list(tmp_path.glob(".chordatlas-metadata-*.tmp")) == []


def test_compare_metadata_reports_replace_and_cleanup_failures(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    metadata_path.write_text("ORIGINAL\n", encoding="utf-8")
    real_unlink = os.unlink

    monkeypatch.setattr(
        os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    def fail_staged_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-metadata-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_staged_unlink)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Temporary cleanup failed:" in captured.err
    assert "cleanup denied" in captured.err
    assert "Unable to write metadata JSON: replace failed" in captured.err
    assert metadata_path.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert len(list(tmp_path.glob(".chordatlas-metadata-*.tmp"))) == 1


def test_compare_metadata_reports_parent_descriptor_close_after_publication(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Metadata JSON written, but temporary cleanup failed:" in captured.err
    assert "unable to close destination directory: close failed" in captured.err
    assert '"metadata_schema_version": "1.0.0"' in metadata_path.read_text(
        encoding="utf-8"
    )


def test_compare_metadata_invalid_filter_does_not_create_output_parent(
    tmp_path, capsys
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "missing" / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--recording",
                "missing",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid comparison filter:" in captured.err
    assert not metadata_path.parent.exists()


def test_compare_metadata_generation_oserror_is_contained_before_filesystem_mutation(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    metadata_path = tmp_path / "missing" / "metadata.json"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    def fail_generation(*args, **kwargs):
        raise OSError("generation failed")

    monkeypatch.setattr(cli, "comparison_metadata_to_json", fail_generation)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Unable to write metadata JSON: generation failed\n"
    assert not metadata_path.parent.exists()


@pytest.mark.parametrize("alias_kind", ["same-path", "relative-alias", "symlink", "hard-link"])
def test_compare_metadata_sidecar_cannot_alias_input_chart(
    tmp_path, capsys, monkeypatch, alias_kind
) -> None:
    chart_path = tmp_path / "song.yaml"
    original = _provenance_comparison_chart_yaml()
    chart_path.write_text(original, encoding="utf-8")
    if alias_kind == "same-path":
        metadata_path = chart_path
    elif alias_kind == "relative-alias":
        monkeypatch.chdir(tmp_path)
        metadata_path = Path("missing") / ".." / "song.yaml"
    elif alias_kind == "symlink":
        metadata_path = tmp_path / "metadata.json"
        metadata_path.symlink_to(chart_path)
    else:
        metadata_path = tmp_path / "metadata.json"
        os.link(chart_path, metadata_path)

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "--metadata-json must not overwrite the input chart\n"
    assert chart_path.read_text(encoding="utf-8") == original
    if alias_kind == "relative-alias":
        assert not (tmp_path / "missing").exists()


def test_compare_metadata_alias_with_missing_parent_and_case_variant(
    tmp_path, capsys, monkeypatch
) -> None:
    chart_path = tmp_path / "song.yaml"
    original = _provenance_comparison_chart_yaml()
    chart_path.write_text(original, encoding="utf-8")
    case_variant = tmp_path / "SONG.yaml"
    try:
        case_insensitive = chart_path.samefile(case_variant)
    except FileNotFoundError:
        case_insensitive = False
    if not case_insensitive:
        pytest.skip("filesystem is case-sensitive")
    monkeypatch.chdir(tmp_path)
    metadata_path = Path("missing") / ".." / "SONG.yaml"

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "--metadata-json must not overwrite the input chart\n"
    assert chart_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "missing").exists()


def test_compare_metadata_path_preserves_directory_symlink_dotdot_semantics(
    tmp_path, capsys
) -> None:
    chart_path = tmp_path / "song.yaml"
    original = _provenance_comparison_chart_yaml()
    chart_path.write_text(original, encoding="utf-8")
    inner = tmp_path / "other" / "inner"
    inner.mkdir(parents=True)
    (tmp_path / "link").symlink_to(inner, target_is_directory=True)
    metadata_path = tmp_path / "link" / ".." / "song.yaml"
    resolved_metadata = tmp_path / "other" / "song.yaml"

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("category,category_label,recording_id")
    assert captured.err == ""
    assert chart_path.read_text(encoding="utf-8") == original
    assert '"metadata_schema_version": "1.0.0"' in resolved_metadata.read_text(
        encoding="utf-8"
    )


def test_compare_research_example_outputs_filtered_csv_and_metadata_sidecar(
    tmp_path,
    capsys,
) -> None:
    metadata_path = tmp_path / "research.metadata.json"

    assert (
        main(
            [
                "compare",
                RESEARCH_EXAMPLE,
                "--format",
                "csv",
                "--provenance",
                "research",
                "--category",
                "effects",
                "--recording",
                "remaster",
                "--severity",
                "medium",
                "--source-specific-only",
                "--metadata-json",
                str(metadata_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific,confidence,claim_origin,provenance_summary,"
        "evidence_refs,recording_source_url,recording_version_label"
    )
    assert "Brighter high-end EQ" in captured.out
    assert "https://example.invalid/audio/remaster,2018 remaster" in captured.out
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"metadata_schema_version": "1.0.0"' in metadata
    assert '"categories": [' in metadata
    assert '"effects"' in metadata
    assert '"ref_id": "remaster-booklet"' in metadata
    assert captured.err == ""


def test_compare_rejects_metadata_json_for_non_csv_output(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "json",
                "--provenance",
                "research",
                "--metadata-json",
                str(tmp_path / "metadata.json"),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--metadata-json is only supported with --format csv" in captured.err


def test_compare_rejects_metadata_json_without_research_provenance(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--metadata-json",
                str(tmp_path / "metadata.json"),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--metadata-json requires --provenance research" in captured.err


def test_compare_reports_unwritable_metadata_json_path(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_provenance_comparison_chart_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "compare",
                str(chart_path),
                "--format",
                "csv",
                "--provenance",
                "research",
                "--metadata-json",
                str(tmp_path),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unable to write metadata JSON:" in captured.err


def test_compare_reports_invalid_recording_filter(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(_comparison_chart_yaml(), encoding="utf-8")

    assert main(["compare", str(chart_path), "--recording", "missing"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid comparison filter: Unknown recording filter(s): missing" in captured.err


def test_compare_reports_malformed_yaml(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["compare", str(chart_path), "--format", "json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid chart: Expected YAML mapping" in captured.err


def test_validate_accepts_valid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\nsections: []\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped" not in captured.err
    assert captured.out == ""


def test_validate_reports_provenance_warnings(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "provenance:",
                "  source_type: unknown",
                "  claim_origin: unknown",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart with 1 provenance warning(s):" in captured.err
    assert "- chart has unknown provenance" in captured.err
    assert captured.out == ""


def test_validate_reports_schema_failure_and_provenance_warnings(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "provenance:",
                "  source_type: unknown",
                "  claim_origin: unknown",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "chordatlas.cli.validate_chart_schema",
        lambda chart: SchemaValidationResult(status="failed", errors=("sections: missing",)),
    )

    assert main(["validate", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert "Invalid chart: normalized JSON Schema validation failed" in captured.err
    assert "- sections: missing" in captured.err
    assert "Provenance warning(s): 1" in captured.err
    assert "- chart has unknown provenance" in captured.err
    assert captured.out == ""


def test_validate_reports_schema_skip(tmp_path, capsys, monkeypatch) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\nsections: []\n", encoding="utf-8")

    monkeypatch.setattr(
        "chordatlas.cli.validate_chart_schema",
        lambda chart: SchemaValidationResult(
            status="skipped",
            reason="jsonschema is not installed",
        ),
    )

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert "Schema validation skipped: jsonschema is not installed" in captured.err
    assert captured.out == ""


def test_validate_rejects_invalid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert "Invalid chart: Expected YAML mapping" in captured.err
    assert captured.out == ""


def test_schemas_check_accepts_clean_mirror(capsys) -> None:
    assert main(["schemas", "--check"]) == 0

    captured = capsys.readouterr()
    assert "Schema mirror is in sync:" in captured.err
    assert captured.out == ""


def test_schemas_check_reports_drift(tmp_path, capsys, monkeypatch) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    schema_dir.joinpath("song-chart.schema.json").write_text("{}", encoding="utf-8")
    schema_dir.joinpath("provenance-record.schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--check"]) == 1

    captured = capsys.readouterr()
    assert "Schema mirror drift detected:" in captured.err
    assert "- song-chart.schema.json" in captured.err
    assert "chordchart schemas --sync" in captured.err
    assert captured.out == ""


def test_schemas_sync_updates_drifted_mirror(tmp_path, capsys, monkeypatch) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    schema_dir.joinpath("song-chart.schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--sync"]) == 0
    assert main(["schemas", "--check"]) == 0

    captured = capsys.readouterr()
    assert "Synced schema mirror:" in captured.err
    assert "Schema mirror is in sync:" in captured.err
    assert captured.out == ""


def test_schemas_check_contains_operational_failure(capsys, monkeypatch) -> None:
    def fail_check():
        raise PermissionError("check denied")

    monkeypatch.setattr("chordatlas.cli.check_schema_mirror", fail_check)

    assert main(["schemas", "--check"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Schema mirror check failed: check denied\n"


def test_schemas_sync_contains_partial_operational_failure(capsys, monkeypatch) -> None:
    def fail_sync():
        raise OSError("second write failed")

    monkeypatch.setattr("chordatlas.cli.sync_schema_mirror", fail_sync)

    assert main(["schemas", "--sync"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Schema mirror sync failed; mirror may be partially updated: second write failed\n"
    )
    assert "Synced schema mirror" not in captured.err


def test_schemas_sync_reports_real_partial_batch_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    for name in SCHEMA_NAMES:
        (schema_dir / name).write_text(f"ORIGINAL {name}\n", encoding="utf-8")
    real_replace = fs._replace_text_at

    def fail_second(parent_fd, leaf, content, **kwargs):
        if leaf == "provenance-record.schema.json":
            raise OSError("second write failed")
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(fs, "_replace_text_at", fail_second)
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--sync"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Schema mirror sync failed; mirror may be partially updated: second write failed\n"
    )
    assert "Synced schema mirror" not in captured.err


def test_schemas_sync_reports_parent_cleanup_after_publication(
    tmp_path, capsys, monkeypatch
) -> None:
    (tmp_path / "schemas").mkdir()
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)
    monkeypatch.chdir(tmp_path)

    assert main(["schemas", "--sync"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Schema mirror synced, but temporary cleanup failed: "
        "unable to close destination directory: close failed\n"
    )


def test_snapshots_list_outputs_targets(capsys) -> None:
    assert main(["snapshots", "list"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "open-string\nresearch-comparison\n"
    assert captured.err == ""


@pytest.mark.parametrize("surface", ["list", "json"])
@pytest.mark.parametrize("failure_point", ["write", "flush"])
@pytest.mark.parametrize("failure_kind", ["broken-pipe", "os-error"])
def test_snapshot_application_outputs_contain_stdout_failures(
    tmp_path, monkeypatch, surface, failure_point, failure_kind
) -> None:
    if surface == "json":
        workspace = _copy_snapshot_workspace(tmp_path)
        monkeypatch.chdir(workspace)
        command = ["snapshots", "check", "all", "--format", "json"]
    else:
        command = ["snapshots", "list"]
    neutralized = []
    stderr = io.StringIO()

    class FailingOutput:
        def write(self, content: str) -> int:
            if failure_point == "write":
                self._fail()
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                self._fail()

        def _fail(self) -> None:
            if failure_kind == "broken-pipe":
                raise BrokenPipeError("broken pipe")
            raise OSError("denied")

    monkeypatch.setattr(cli.sys, "stdout", FailingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli, "_neutralize_broken_stdout", lambda: neutralized.append("called")
    )

    assert main(command) == 1

    if failure_kind == "broken-pipe":
        assert stderr.getvalue() == ""
        assert neutralized == ["called"]
    else:
        assert stderr.getvalue() == "Unable to write output: denied\n"
        assert neutralized == []


@pytest.mark.parametrize("surface", ["list", "json"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_snapshot_application_output_process_control_propagates(
    tmp_path, monkeypatch, surface, interrupt
) -> None:
    if surface == "json":
        workspace = _copy_snapshot_workspace(tmp_path)
        monkeypatch.chdir(workspace)
        command = ["snapshots", "check", "all", "--format", "json"]
    else:
        command = ["snapshots", "list"]

    def stop(content: str) -> int:
        raise interrupt

    monkeypatch.setattr(cli, "_write_stdout", stop)

    with pytest.raises(interrupt):
        main(command)


@pytest.mark.parametrize(
    "command",
    [
        ["snapshots", "list"],
        ["snapshots", "check", "all", "--format", "json"],
    ],
    ids=["list", "json"],
)
def test_snapshot_application_subprocess_contains_closed_stdout(command) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    process = subprocess.Popen(
        [sys.executable, "-m", "chordatlas.cli", *command],
        cwd=ROOT,
        env=env,
        stdout=write_fd,
        stderr=subprocess.PIPE,
    )
    os.close(write_fd)
    assert process.stderr is not None

    assert process.wait(timeout=10) == 1
    stderr = process.stderr.read().decode("utf-8")
    assert stderr == ""


def test_snapshots_json_operational_failure_does_not_publish_empty_output(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "tests" / "snapshots").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    published = []
    monkeypatch.setattr(cli, "_write_stdout", lambda content: published.append(content) or 0)

    assert main(["snapshots", "check", "open-string", "--format", "json"]) == 1

    assert published == []


def test_snapshots_json_stdout_failure_preserves_completed_diff_artifact(
    tmp_path, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "open-string-progression.md"
    diff_dir = workspace / "diffs"
    snapshot.write_text("drift\n", encoding="utf-8")
    stderr = io.StringIO()

    class FailingOutput:
        def write(self, content: str) -> int:
            raise OSError("denied")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(cli.sys, "stdout", FailingOutput())
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert (
        main(
            [
                "snapshots",
                "check",
                "open-string",
                "--format",
                "json",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    assert stderr.getvalue() == "Unable to write output: denied\n"
    assert (diff_dir / "open-string-progression.md.diff").exists()


def test_snapshots_check_accepts_clean_target(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshots are in sync: research-comparison" in captured.err


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_snapshots_check_contains_missing_source_fixture(
    tmp_path, capsys, monkeypatch, output_format
) -> None:
    (tmp_path / "tests" / "snapshots").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    command = ["snapshots", "check", "open-string"]
    if output_format == "json":
        command.extend(["--format", "json"])

    assert main(command) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot check failed:" in captured.err
    assert "open-string-progression.yaml" in captured.err
    assert "Traceback" not in captured.err


def test_snapshots_check_classifies_malformed_source_as_operational_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    (workspace / "examples" / "open-string-progression.yaml").write_text(
        "title: [\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "open-string"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot check failed: Invalid YAML" in captured.err


def test_snapshots_check_contains_diff_artifact_write_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "open-string-progression.md"
    snapshot.write_text("drift\n", encoding="utf-8")
    blocked = workspace / "blocked-diffs"
    blocked.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "open-string",
                "--diff-dir",
                str(blocked),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot drift detected: open-string" in captured.err
    assert "- open-string-progression.md" in captured.err
    assert "--- snapshot/open-string-progression.md" in captured.err
    assert "Snapshot check failed:" in captured.err
    assert "no diff artifacts were written" in captured.err
    assert "may be partially updated" not in captured.err
    assert captured.err.index("Snapshot drift detected:") < captured.err.index(
        "Snapshot check failed:"
    )
    assert "Run `chordchart snapshots regenerate`" not in captured.err
    assert "Full diffs written" not in captured.err


def test_snapshots_json_parent_setup_failure_reports_zero_publication(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    (workspace / "tests" / "snapshots" / name).write_text(
        "drift\n", encoding="utf-8"
    )
    blocked = workspace / "blocked-diffs"
    blocked.write_text("PRESERVE\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--format",
                "json",
                "--diff-dir",
                str(blocked),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no diff artifacts were written" in captured.err
    assert "may be partially updated" not in captured.err
    assert blocked.read_text(encoding="utf-8") == "PRESERVE\n"


def test_snapshots_check_outputs_clean_json(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison", "--format", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "clean"
    assert payload["target"] == "research-comparison"
    assert payload["drift_files"] == []
    assert payload["diffs"] == []
    assert "research-recording-comparison.effects-remaster.csv" in payload["checked_files"]
    assert captured.out == json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert captured.err == ""


def test_snapshots_check_reports_drift(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot drift detected: research-comparison" in captured.err
    assert "- research-recording-comparison.effects-remaster.csv" in captured.err
    assert "--- snapshot/research-recording-comparison.effects-remaster.csv" in captured.err
    assert "+++ generated/research-recording-comparison.effects-remaster.csv" in captured.err
    assert "-drift" in captured.err


def test_snapshots_check_reports_missing_golden_as_addition(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "open-string-progression.md"
    snapshot = workspace / "tests" / "snapshots" / name
    diff_dir = workspace / "snapshot-diffs"
    snapshot.unlink()
    monkeypatch.chdir(workspace)

    result = check_snapshots(workspace, target="open-string")
    assert result.dirty
    assert name in result.drift
    assert (
        main(
            [
                "snapshots",
                "check",
                "open-string",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    diff = (diff_dir / f"{name}.diff").read_text(encoding="utf-8")
    assert captured.out == ""
    assert f"- {name}" in captured.err
    assert "Traceback" not in captured.err
    assert f"--- snapshot/{name}" in diff
    assert f"+++ generated/{name}" in diff
    assert "@@ -0,0 +1" in diff


def test_missing_empty_snapshot_is_still_drift(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        snapshots,
        "_snapshot_files",
        lambda root, target: (snapshots.SnapshotFile("empty.txt", lambda: ""),),
    )

    result = snapshots.check_snapshots(tmp_path, target="open-string")

    assert result.dirty
    assert result.drift == ("empty.txt",)


def test_snapshots_check_reports_missing_golden_in_json(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "open-string-progression.md"
    (workspace / "tests" / "snapshots" / name).unlink()
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "open-string", "--format", "json"]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "dirty"
    assert payload["drift_files"] == [name]
    assert payload["diffs"][0]["file"] == name
    assert captured.err == ""


def test_snapshots_check_outputs_drift_json(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "research-comparison", "--format", "json"]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "dirty"
    assert payload["target"] == "research-comparison"
    assert payload["drift_files"] == ["research-recording-comparison.effects-remaster.csv"]
    assert payload["diffs"][0]["file"] == "research-recording-comparison.effects-remaster.csv"
    assert payload["diffs"][0]["truncated"] is False
    assert payload["diffs"][0]["diff_artifact"] is None
    assert captured.err == ""


def test_snapshots_check_writes_diff_artifacts(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    diff_dir = workspace / "snapshot-diffs"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    diff_path = diff_dir / "research-recording-comparison.effects-remaster.csv.diff"
    assert diff_path.exists()
    assert "--- snapshot/research-recording-comparison.effects-remaster.csv" in diff_path.read_text(
        encoding="utf-8"
    )
    assert f"Full diffs written to: {diff_dir}" in captured.err


@pytest.mark.parametrize(
    "entry_kind", ["live-link", "dangling-link", "directory", "hardlink", "fifo"]
)
def test_diff_artifact_policy_refuses_unsafe_existing_leaf(
    tmp_path, entry_kind
) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    artifact = diff_dir / "first.txt.diff"
    referent = tmp_path / "referent.diff"
    peer = tmp_path / "peer.diff"
    if entry_kind == "live-link":
        referent.write_text("REFERENT\n", encoding="utf-8")
        artifact.symlink_to(referent)
    elif entry_kind == "dangling-link":
        artifact.symlink_to(referent)
    elif entry_kind == "directory":
        artifact.mkdir()
        (artifact / "marker").write_text("PRESERVE\n", encoding="utf-8")
    elif entry_kind == "hardlink":
        peer.write_text("PEER\n", encoding="utf-8")
        os.link(peer, artifact)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        os.mkfifo(artifact)

    with pytest.raises(snapshots.DiffArtifactPolicyError) as raised:
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt"),
        )

    assert str(artifact) in str(raised.value)
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))
    if entry_kind == "live-link":
        assert artifact.is_symlink()
        assert referent.read_text(encoding="utf-8") == "REFERENT\n"
    elif entry_kind == "dangling-link":
        assert artifact.is_symlink()
        assert not referent.exists()
    elif entry_kind == "directory":
        assert (artifact / "marker").read_text(encoding="utf-8") == "PRESERVE\n"
    elif entry_kind == "hardlink":
        assert artifact.stat().st_ino == peer.stat().st_ino
        assert peer.read_text(encoding="utf-8") == "PEER\n"
    else:
        assert stat.S_ISFIFO(artifact.lstat().st_mode)


def test_diff_artifact_policy_preflights_every_leaf_before_publication(tmp_path) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    first = diff_dir / "first.txt.diff"
    blocked = diff_dir / "second.txt.diff"
    first.write_text("ORIGINAL\n", encoding="utf-8")
    first_inode = first.stat().st_ino
    blocked.mkdir()

    with pytest.raises(snapshots.DiffArtifactPolicyError):
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt", "second.txt"),
        )

    assert first.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert first.stat().st_ino == first_inode
    assert blocked.is_dir()
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


def test_diff_artifact_atomic_publication_preserves_mode_and_umask(
    tmp_path, monkeypatch
) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    existing = diff_dir / "existing.txt.diff"
    missing = diff_dir / "missing.txt.diff"
    existing.write_text("ORIGINAL\n", encoding="utf-8")
    existing.chmod(0o640)
    existing_inode = existing.stat().st_ino
    observed = _record_content_stage_modes(monkeypatch)
    previous_umask = os.umask(0o027)
    try:
        selected_dir, paths = snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("existing.txt", "missing.txt"),
        )
    finally:
        os.umask(previous_umask)

    assert selected_dir == diff_dir
    assert paths == {
        "existing.txt": str(existing),
        "missing.txt": str(missing),
    }
    assert existing.read_text(encoding="utf-8") == "DIFF existing.txt\n"
    assert missing.read_text(encoding="utf-8") == "DIFF missing.txt\n"
    assert existing.stat().st_ino != existing_inode
    assert existing.stat().st_mode & 0o777 == 0o640
    assert missing.stat().st_mode & 0o777 == 0o640
    assert observed and all(mode == 0o600 for _, mode in observed)
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


def test_diff_artifact_parent_symlink_is_selected_once_and_opened_once(
    tmp_path, monkeypatch
) -> None:
    original = tmp_path / "original"
    retargeted = tmp_path / "retargeted"
    original.mkdir()
    retargeted.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(original, target_is_directory=True)
    real_open = fs.os.open
    parent_opens = 0

    def retarget_then_open(path, flags, *args, **kwargs):
        nonlocal parent_opens
        if Path(path) == original and flags & getattr(os, "O_DIRECTORY", 0):
            parent_opens += 1
            linked.unlink()
            linked.symlink_to(retargeted, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(fs.os, "open", retarget_then_open)

    selected_dir, paths = snapshots._publish_diff_artifacts(
        linked,
        _prepared_diff_artifacts("first.txt", "second.txt"),
    )

    assert parent_opens == 1
    assert selected_dir == original
    assert paths == {
        "first.txt": str(original / "first.txt.diff"),
        "second.txt": str(original / "second.txt.diff"),
    }
    assert (original / "first.txt.diff").read_text(encoding="utf-8") == (
        "DIFF first.txt\n"
    )
    assert (original / "second.txt.diff").read_text(encoding="utf-8") == (
        "DIFF second.txt\n"
    )
    assert list(retargeted.iterdir()) == []


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_diff_artifact_parent_retarget_reports_frozen_physical_destination(
    tmp_path, capsys, monkeypatch, output_format
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    (workspace / "tests" / "snapshots" / name).write_text(
        "drift\n", encoding="utf-8"
    )
    original = tmp_path / "original"
    retargeted = tmp_path / "retargeted"
    original.mkdir()
    retargeted.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(original, target_is_directory=True)
    real_open = fs.os.open
    switched = False

    def retarget_then_open(path, flags, *args, **kwargs):
        nonlocal switched
        if not switched and Path(path) == original and flags & getattr(os, "O_DIRECTORY", 0):
            switched = True
            linked.unlink()
            linked.symlink_to(retargeted, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(fs.os, "open", retarget_then_open)
    monkeypatch.chdir(workspace)
    command = [
        "snapshots",
        "check",
        "research-comparison",
        "--diff-dir",
        str(linked),
    ]
    if output_format == "json":
        command.extend(["--format", "json"])

    assert main(command) == 1

    captured = capsys.readouterr()
    artifact = original / f"{name}.diff"
    assert switched
    assert artifact.is_file()
    assert list(retargeted.iterdir()) == []
    if output_format == "text":
        assert captured.out == ""
        assert f"Full diffs written to: {original}\n" in captured.err
        assert f"Full diffs written to: {linked}" not in captured.err
    else:
        payload = json.loads(captured.out)
        assert payload["diffs"][0]["diff_artifact"] == str(artifact)
        assert captured.err == ""


def test_snapshot_diff_generation_finishes_before_diff_directory_creation(
    tmp_path, monkeypatch
) -> None:
    result = snapshots.SnapshotResult(
        status="dirty",
        root=tmp_path,
        target="all",
        checked=("first.txt", "second.txt"),
        drift=("first.txt", "second.txt"),
    )
    calls = []

    def render(root, name):
        calls.append(name)
        if name == "second.txt":
            raise OSError("diff generation failed")
        return ["FIRST\n"]

    monkeypatch.setattr(snapshots, "_snapshot_diff", render)

    with pytest.raises(OSError, match="diff generation failed"):
        snapshots._prepare_snapshot_diffs(tmp_path, result)

    assert calls == ["first.txt", "second.txt"]
    assert not (tmp_path / "diffs").exists()


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        (("../escape.txt",), "Invalid snapshot artifact name"),
        (("bad\0name.txt",), "Invalid snapshot artifact name"),
        (("duplicate.txt", "duplicate.txt"), "Duplicate snapshot artifact name"),
    ],
)
def test_invalid_diff_artifact_registration_fails_before_directory_creation(
    tmp_path, capsys, monkeypatch, drift, message
) -> None:
    result = snapshots.SnapshotResult(
        status="dirty",
        root=tmp_path,
        target="all",
        checked=drift,
        drift=drift,
    )
    diff_dir = tmp_path / "diffs"
    monkeypatch.setattr(snapshots, "check_snapshots", lambda root, target: result)
    monkeypatch.setattr(snapshots, "_snapshot_diff", lambda root, name: ["DIFF\n"])
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "snapshots",
                "check",
                "--format",
                "json",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert not diff_dir.exists()


def test_snapshots_json_policy_failure_has_no_payload_or_side_effect(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    snapshot = workspace / "tests" / "snapshots" / name
    diff_dir = workspace / "snapshot-diffs"
    referent = workspace / "external.diff"
    snapshot.write_text("drift\n", encoding="utf-8")
    diff_dir.mkdir()
    referent.write_text("REFERENT\n", encoding="utf-8")
    (diff_dir / f"{name}.diff").symlink_to(referent)
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--format",
                "json",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no diff artifacts were written" in captured.err
    assert str(diff_dir / f"{name}.diff") in captured.err
    assert referent.read_text(encoding="utf-8") == "REFERENT\n"


def test_snapshots_text_policy_failure_keeps_inline_drift_diagnostic(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    snapshot = workspace / "tests" / "snapshots" / name
    diff_dir = workspace / "snapshot-diffs"
    referent = workspace / "external.diff"
    snapshot.write_text("drift\n", encoding="utf-8")
    diff_dir.mkdir()
    referent.write_text("REFERENT\n", encoding="utf-8")
    (diff_dir / f"{name}.diff").symlink_to(referent)
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot drift detected: research-comparison" in captured.err
    assert f"- {name}" in captured.err
    assert f"--- snapshot/{name}" in captured.err
    assert "no diff artifacts were written" in captured.err
    assert captured.err.index("Snapshot drift detected:") < captured.err.index(
        "Snapshot check failed:"
    )
    assert "Run `chordchart snapshots regenerate`" not in captured.err
    assert "Full diffs written" not in captured.err
    assert referent.read_text(encoding="utf-8") == "REFERENT\n"


def test_diff_artifact_runtime_failure_reports_partial_batch(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    diff_dir = workspace / "snapshot-diffs"
    names = (
        "open-string-progression.md",
        "open-string-progression.txt",
        "open-string-progression.json",
    )
    for name in names:
        (workspace / "tests" / "snapshots" / name).write_text(
            "drift\n", encoding="utf-8"
        )
    diff_dir.mkdir()
    for name in names:
        (diff_dir / f"{name}.diff").write_text("ORIGINAL\n", encoding="utf-8")
    real_replace = fs.os.replace
    replaces = 0

    def fail_second(source, target, **kwargs):
        nonlocal replaces
        replaces += 1
        if replaces == 2:
            raise OSError("second replace failed")
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(fs.os, "replace", fail_second)
    monkeypatch.chdir(workspace)

    assert (
        main(
            ["snapshots", "check", "open-string", "--diff-dir", str(diff_dir)]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot drift detected: open-string" in captured.err
    for name in names:
        assert f"- {name}" in captured.err
    assert "diff artifacts may be partially updated" in captured.err
    assert "second replace failed" in captured.err
    assert captured.err.index("Snapshot drift detected:") < captured.err.index(
        "diff artifacts may be partially updated"
    )
    assert "Run `chordchart snapshots regenerate`" not in captured.err
    assert "Full diffs written" not in captured.err
    assert (diff_dir / f"{names[0]}.diff").read_text(encoding="utf-8").startswith(
        "--- snapshot/"
    )
    assert (diff_dir / f"{names[1]}.diff").read_text(encoding="utf-8") == (
        "ORIGINAL\n"
    )
    assert (diff_dir / f"{names[2]}.diff").read_text(encoding="utf-8") == (
        "ORIGINAL\n"
    )
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_diff_artifact_first_replace_failure_reports_zero_publication(
    tmp_path, capsys, monkeypatch, output_format
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    (workspace / "tests" / "snapshots" / name).write_text(
        "drift\n", encoding="utf-8"
    )
    diff_dir = workspace / "snapshot-diffs"
    diff_dir.mkdir()
    artifact = diff_dir / f"{name}.diff"
    artifact.write_text("ORIGINAL\n", encoding="utf-8")
    monkeypatch.setattr(
        fs.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace denied")),
    )
    monkeypatch.chdir(workspace)
    command = [
        "snapshots",
        "check",
        "research-comparison",
        "--diff-dir",
        str(diff_dir),
    ]
    if output_format == "json":
        command.extend(["--format", "json"])

    assert main(command) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    if output_format == "text":
        assert "Snapshot drift detected: research-comparison" in captured.err
    assert "no diff artifacts were written" in captured.err
    assert "replace denied" in captured.err
    assert "may be partially updated" not in captured.err
    assert artifact.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


def test_batch_publish_observer_receives_completed_leaves_in_order(tmp_path) -> None:
    published = []

    fs.replace_text_batch(
        tmp_path,
        (("first.txt", "FIRST\n"), ("second.txt", "SECOND\n")),
        stage_prefix=".chordatlas-observer-",
        on_published=published.append,
    )

    assert published == ["first.txt", "second.txt"]
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "FIRST\n"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "SECOND\n"


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(7)])
def test_diff_artifact_process_control_propagates_and_cleans_stage(
    tmp_path, monkeypatch, control
) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    artifact = diff_dir / "first.txt.diff"
    artifact.write_text("ORIGINAL\n", encoding="utf-8")

    def stop(*args, **kwargs):
        raise control

    monkeypatch.setattr(fs.os, "replace", stop)

    with pytest.raises(type(control)) as raised:
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt"),
        )

    assert raised.value is control
    assert artifact.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


@pytest.mark.parametrize("existing", [False, True])
def test_diff_artifact_stage_collision_exhaustion_preserves_state(
    tmp_path, monkeypatch, existing
) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    artifact = diff_dir / "first.txt.diff"
    if existing:
        artifact.write_text("ORIGINAL\n", encoding="utf-8")
        collision = diff_dir / ".chordatlas-diff-fixed.tmp"
    else:
        collision = diff_dir / ".chordatlas-diff-mode-fixed.tmp"
    collision.write_text("COLLISION\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    with pytest.raises(OSError, match="unable to allocate a private"):
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt"),
        )

    assert artifact.exists() is existing
    if existing:
        assert artifact.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert collision.read_text(encoding="utf-8") == "COLLISION\n"


def test_diff_artifact_replace_and_cleanup_failures_are_both_reported(
    tmp_path, monkeypatch
) -> None:
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    artifact = diff_dir / "first.txt.diff"
    artifact.write_text("ORIGINAL\n", encoding="utf-8")
    real_unlink = fs.os.unlink
    monkeypatch.setattr(
        fs.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    def fail_stage_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-diff-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "unlink", fail_stage_unlink)

    with pytest.raises(OSError, match="replace failed") as raised:
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt"),
        )

    assert raised.value.__notes__
    assert "temporary file .chordatlas-diff-" in raised.value.__notes__[0]
    assert "cleanup denied" in raised.value.__notes__[0]
    assert artifact.read_text(encoding="utf-8") == "ORIGINAL\n"
    stages = list(diff_dir.glob(".chordatlas-diff-*.tmp"))
    assert len(stages) == 1


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_diff_artifact_parent_close_failure_reports_fully_published_batch(
    tmp_path, capsys, monkeypatch, output_format
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    diff_dir = workspace / "snapshot-diffs"
    names = (
        "open-string-progression.md",
        "open-string-progression.txt",
        "open-string-progression.json",
    )
    for name in names:
        (workspace / "tests" / "snapshots" / name).write_text(
            "drift\n", encoding="utf-8"
        )
    real_close = fs.os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(fs.os, "close", close_then_fail)
    monkeypatch.chdir(workspace)
    command = [
        "snapshots",
        "check",
        "open-string",
        "--diff-dir",
        str(diff_dir),
    ]
    if output_format == "json":
        command.extend(["--format", "json"])

    assert main(command) == 1

    captured = capsys.readouterr()
    if output_format == "text":
        assert captured.out == ""
        assert "Snapshot drift detected: open-string" in captured.err
    else:
        assert captured.out == ""
    assert f"diff artifacts were written to {diff_dir}" in captured.err
    assert "temporary cleanup failed" in captured.err
    assert "unable to close destination directory: close failed" in captured.err
    assert "may be partially updated" not in captured.err
    assert "Run `chordchart snapshots regenerate`" not in captured.err
    assert "Full diffs written" not in captured.err
    for name in names:
        artifact = diff_dir / f"{name}.diff"
        assert artifact.read_text(encoding="utf-8").startswith("--- snapshot/")
    assert not list(diff_dir.glob(".chordatlas-diff-*.tmp"))


def test_diff_artifact_published_cleanup_error_preserves_cause_and_directory(
    tmp_path, monkeypatch
) -> None:
    diff_dir = tmp_path / "diffs"
    real_close = fs.os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(fs.os, "close", close_then_fail)

    with pytest.raises(snapshots.DiffArtifactsPublishedCleanupError) as raised:
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt", "second.txt"),
        )

    assert raised.value.directory == diff_dir
    assert isinstance(raised.value.__cause__, fs.PublishedCleanupError)
    assert (diff_dir / "first.txt.diff").is_file()
    assert (diff_dir / "second.txt.diff").is_file()


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(7)])
def test_diff_artifact_parent_close_process_control_propagates_after_publication(
    tmp_path, monkeypatch, control
) -> None:
    diff_dir = tmp_path / "diffs"
    real_close = fs.os.close

    def close_then_stop(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        real_close(descriptor)
        if is_directory:
            raise control

    monkeypatch.setattr(fs.os, "close", close_then_stop)

    with pytest.raises(type(control)) as raised:
        snapshots._publish_diff_artifacts(
            diff_dir,
            _prepared_diff_artifacts("first.txt", "second.txt"),
        )

    assert raised.value is control
    assert (diff_dir / "first.txt.diff").is_file()
    assert (diff_dir / "second.txt.diff").is_file()


def test_diff_artifact_policy_failure_reports_parent_cleanup_note(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "research-recording-comparison.effects-remaster.csv"
    (workspace / "tests" / "snapshots" / name).write_text(
        "drift\n", encoding="utf-8"
    )
    diff_dir = workspace / "snapshot-diffs"
    diff_dir.mkdir()
    (diff_dir / f"{name}.diff").mkdir()
    real_close = fs.os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(fs.os, "close", close_then_fail)
    monkeypatch.chdir(workspace)

    assert (
        main(
            ["snapshots", "check", "research-comparison", "--diff-dir", str(diff_dir)]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "no diff artifacts were written" in captured.err
    assert "Temporary cleanup failed: close failed" in captured.err
    assert (diff_dir / f"{name}.diff").is_dir()


def test_snapshots_check_json_reports_diff_artifact_paths(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    diff_dir = workspace / "snapshot-diffs"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert (
        main(
            [
                "snapshots",
                "check",
                "research-comparison",
                "--format",
                "json",
                "--diff-dir",
                str(diff_dir),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact = Path(payload["diffs"][0]["diff_artifact"])
    assert artifact.exists()
    assert artifact == (diff_dir / "research-recording-comparison.effects-remaster.csv.diff")
    assert captured.err == ""


def test_snapshots_check_truncates_large_inline_diffs(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "open-string-progression.md"
    snapshot.write_text(
        "\n".join(f"drift line {index}" for index in range(200)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "open-string"]) == 1

    captured = capsys.readouterr()
    assert "... diff truncated," in captured.err


def test_snapshots_regenerate_updates_drifted_target(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "research-recording-comparison.effects-remaster.csv"
    snapshot.write_text("drift\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "regenerate", "research-comparison"]) == 0
    assert main(["snapshots", "check", "research-comparison"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Regenerated snapshots: research-comparison" in captured.err
    assert "Snapshots are in sync: research-comparison" in captured.err


def test_snapshots_regenerate_recreates_missing_golden(tmp_path, capsys, monkeypatch) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    snapshot = workspace / "tests" / "snapshots" / "open-string-progression.md"
    snapshot.unlink()
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "regenerate", "open-string"]) == 0
    assert snapshot.exists()
    assert main(["snapshots", "check", "open-string"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Regenerated snapshots: open-string" in captured.err
    assert "Snapshots are in sync: open-string" in captured.err


@pytest.mark.parametrize("link_kind", ["live", "dangling"])
def test_snapshots_check_treats_symlink_leaf_as_missing_drift(
    tmp_path, monkeypatch, link_kind
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "open-string-progression.md"
    snapshot = workspace / "tests" / "snapshots" / name
    expected = snapshot.read_text(encoding="utf-8")
    referent = workspace / "external.md"
    if link_kind == "live":
        referent.write_text(expected, encoding="utf-8")
    snapshot.unlink()
    snapshot.symlink_to(referent)

    result = check_snapshots(workspace, target="open-string")

    assert result.dirty
    assert name in result.drift
    assert snapshot.is_symlink()
    if link_kind == "live":
        assert referent.read_text(encoding="utf-8") == expected
    else:
        assert not referent.exists()


def test_snapshot_symlink_drift_uses_addition_diff_in_text_json_and_artifact(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = _copy_snapshot_workspace(tmp_path)
    name = "open-string-progression.md"
    snapshot = workspace / "tests" / "snapshots" / name
    referent = workspace / "external.md"
    referent.write_bytes(snapshot.read_bytes())
    snapshot.unlink()
    snapshot.symlink_to(referent)
    text_diffs = workspace / "text-diffs"
    json_diffs = workspace / "json-diffs"
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "check", "open-string", "--diff-dir", str(text_diffs)]) == 1
    captured = capsys.readouterr()
    artifact = text_diffs / f"{name}.diff"
    assert f"- {name}" in captured.err
    assert "@@ -0,0 +1" in captured.err
    assert "@@ -0,0 +1" in artifact.read_text(encoding="utf-8")

    assert (
        main(
            [
                "snapshots",
                "check",
                "open-string",
                "--format",
                "json",
                "--diff-dir",
                str(json_diffs),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert name in payload["drift_files"]
    assert Path(payload["diffs"][0]["diff_artifact"]).read_text(encoding="utf-8").find(
        "@@ -0,0 +1"
    ) != -1
    assert captured.err == ""
    assert referent.exists()


@pytest.mark.parametrize("link_kind", ["live", "dangling"])
def test_snapshot_regeneration_replaces_symlink_without_touching_referent(
    tmp_path, monkeypatch, link_kind
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    referent = tmp_path / "external.txt"
    if link_kind == "live":
        referent.write_text("REFERENT\n", encoding="utf-8")
    snapshot.symlink_to(referent)
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    snapshots.regenerate_snapshots(tmp_path)

    assert not snapshot.is_symlink()
    assert snapshot.read_text(encoding="utf-8") == "GENERATED first.txt\n"
    if link_kind == "live":
        assert referent.read_text(encoding="utf-8") == "REFERENT\n"
    else:
        assert not referent.exists()


def test_snapshot_regeneration_detaches_hard_link_and_preserves_peer(
    tmp_path, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    peer = tmp_path / "peer.txt"
    peer.write_text("SHARED\n", encoding="utf-8")
    os.link(peer, snapshot)
    shared_inode = peer.stat().st_ino
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    snapshots.regenerate_snapshots(tmp_path)

    assert peer.read_text(encoding="utf-8") == "SHARED\n"
    assert peer.stat().st_ino == shared_inode
    assert snapshot.stat().st_ino != shared_inode
    assert snapshot.read_text(encoding="utf-8") == "GENERATED first.txt\n"


def test_snapshot_regeneration_preserves_supported_regular_mode_bits(
    tmp_path, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    snapshot.write_text("ORIGINAL\n", encoding="utf-8")
    snapshot.chmod(0o4755)
    supported_mode = snapshot.stat().st_mode & 0o7777
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    snapshots.regenerate_snapshots(tmp_path)

    assert snapshot.stat().st_mode & 0o7777 == supported_mode


def test_snapshot_regeneration_new_leaf_honors_umask(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )
    previous_umask = os.umask(0o027)
    try:
        snapshots.regenerate_snapshots(tmp_path)
    finally:
        os.umask(previous_umask)

    assert (tmp_path / "tests" / "snapshots" / "first.txt").stat().st_mode & 0o777 == 0o640


def test_snapshot_regeneration_rejects_directory_leaf_untouched(
    tmp_path, capsys, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    blocked = snapshot_dir / "first.txt"
    blocked.mkdir(parents=True)
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    assert snapshots.run_snapshot_regenerate(root=tmp_path, stderr=sys.stderr) == 1

    captured = capsys.readouterr()
    assert blocked.is_dir()
    assert list(blocked.iterdir()) == []
    assert "snapshots may be partially updated" in captured.err


def test_snapshot_regeneration_keeps_prior_updates_on_later_failure(
    tmp_path, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    names = ("first.txt", "second.txt", "third.txt")
    for name in names:
        (snapshot_dir / name).write_text(f"ORIGINAL {name}\n", encoding="utf-8")
    real_replace = fs._replace_text_at

    def fail_second(parent_fd, leaf, content, **kwargs):
        if leaf == "second.txt":
            raise OSError("second write failed")
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files(*names)
    )
    monkeypatch.setattr(fs, "_replace_text_at", fail_second)

    with pytest.raises(OSError, match="second write failed"):
        snapshots.regenerate_snapshots(tmp_path)

    assert (snapshot_dir / "first.txt").read_text(encoding="utf-8") == "GENERATED first.txt\n"
    for name in names[1:]:
        assert (snapshot_dir / name).read_text(encoding="utf-8") == f"ORIGINAL {name}\n"


@pytest.mark.parametrize("matching_stage", [False, True])
def test_snapshot_regeneration_parent_retarget_stays_descriptor_anchored(
    tmp_path, monkeypatch, matching_stage
) -> None:
    workspace = tmp_path / "workspace"
    tests_dir = workspace / "tests"
    original = tmp_path / "original"
    retargeted = tmp_path / "retargeted"
    tests_dir.mkdir(parents=True)
    original.mkdir()
    retargeted.mkdir()
    linked = tests_dir / "snapshots"
    linked.symlink_to(original, target_is_directory=True)
    names = ("first.txt", "second.txt")
    real_replace = fs._replace_text_at
    first = True

    def retarget_then_replace(parent_fd, leaf, content, **kwargs):
        nonlocal first
        if first:
            first = False
            if matching_stage:
                (retargeted / ".chordatlas-snapshot-fixed.tmp").write_text(
                    "ATTACKER\n", encoding="utf-8"
                )
            linked.unlink()
            linked.symlink_to(retargeted, target_is_directory=True)
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files(*names)
    )
    monkeypatch.setattr(fs, "_replace_text_at", retarget_then_replace)
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")

    snapshots.regenerate_snapshots(workspace)

    for name in names:
        assert (original / name).read_text(encoding="utf-8") == f"GENERATED {name}\n"
        assert not (retargeted / name).exists()
    assert list(original.glob(".chordatlas-snapshot-*.tmp")) == []
    if matching_stage:
        assert (retargeted / ".chordatlas-snapshot-fixed.tmp").read_text(
            encoding="utf-8"
        ) == "ATTACKER\n"


@pytest.mark.parametrize("failure_point", ["write", "flush", "close", "fchmod"])
def test_snapshot_regeneration_stage_failure_preserves_current_leaf(
    tmp_path, monkeypatch, failure_point
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    snapshot.write_text("ORIGINAL\n", encoding="utf-8")
    snapshot.chmod(0o640)
    original = (snapshot.read_bytes(), snapshot.stat().st_ino, snapshot.stat().st_mode)

    class FailingStage:
        def __init__(self, staged: Path) -> None:
            self.staged = staged
            staged.write_text("", encoding="utf-8")

        def __enter__(self):
            return self

        def write(self, content: str) -> int:
            self.staged.write_text("PARTIAL", encoding="utf-8")
            if failure_point == "write":
                raise OSError("stage write failed")
            return len(content)

        def flush(self) -> None:
            if failure_point == "flush":
                raise OSError("stage flush failed")

        def fileno(self) -> int:
            return -1

        def __exit__(self, exc_type, exc, traceback) -> None:
            if failure_point == "close":
                raise OSError("stage close failed")

    def fchmod(descriptor, mode):
        if failure_point == "fchmod":
            raise OSError("stage fchmod failed")

    monkeypatch.setattr(
        fs,
        "_open_stage",
        lambda parent_fd, staged, flags, reporter: FailingStage(snapshot_dir / staged),
    )
    monkeypatch.setattr(os, "fchmod", fchmod)
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    with pytest.raises(OSError, match=f"stage {failure_point} failed"):
        snapshots.regenerate_snapshots(tmp_path)

    assert (snapshot.read_bytes(), snapshot.stat().st_ino, snapshot.stat().st_mode) == original
    assert list(snapshot_dir.glob(".chordatlas-snapshot-*.tmp")) == []


def test_snapshot_regeneration_replace_and_cleanup_failures_are_both_reported(
    tmp_path, capsys, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    snapshot.write_text("ORIGINAL\n", encoding="utf-8")
    real_unlink = os.unlink
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )
    monkeypatch.setattr(
        os, "replace", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed"))
    )

    def fail_stage_unlink(path, *args, **kwargs):
        if _is_content_stage(path, ".chordatlas-snapshot-"):
            raise PermissionError("cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_stage_unlink)

    assert snapshots.run_snapshot_regenerate(root=tmp_path, stderr=sys.stderr) == 1

    captured = capsys.readouterr()
    assert "replace failed" in captured.err
    assert "Temporary cleanup failed:" in captured.err
    assert "cleanup denied" in captured.err
    assert snapshot.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_snapshot_regeneration_parent_close_failure_reports_published_batch(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        _close_parent_then_fail(real_close, descriptor)

    monkeypatch.setattr(os, "close", close_then_fail)

    assert snapshots.run_snapshot_regenerate(root=tmp_path, stderr=sys.stderr) == 1

    captured = capsys.readouterr()
    assert captured.err == (
        "Snapshots regenerated, but temporary cleanup failed: "
        "unable to close destination directory: close failed\n"
    )
    assert (tmp_path / "tests" / "snapshots" / "first.txt").read_text(
        encoding="utf-8"
    ) == "GENERATED first.txt\n"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_snapshot_regeneration_process_control_propagates_and_cleans_stage(
    tmp_path, monkeypatch, interrupt
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    snapshot.write_text("ORIGINAL\n", encoding="utf-8")
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    def stop(*args, **kwargs):
        raise interrupt

    monkeypatch.setattr(os, "replace", stop)

    with pytest.raises(interrupt):
        snapshots.regenerate_snapshots(tmp_path)

    assert snapshot.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert list(snapshot_dir.glob(".chordatlas-snapshot-*.tmp")) == []


def test_snapshot_regeneration_uses_one_parent_descriptor_for_batch(
    tmp_path, monkeypatch
) -> None:
    names = ("first.txt", "second.txt", "third.txt")
    seen = []
    real_replace = fs._replace_text_at

    def record_parent(parent_fd, leaf, content, **kwargs):
        seen.append(parent_fd)
        return real_replace(parent_fd, leaf, content, **kwargs)

    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files(*names)
    )
    monkeypatch.setattr(fs, "_replace_text_at", record_parent)

    snapshots.regenerate_snapshots(tmp_path)

    assert len(seen) == len(names)
    assert len(set(seen)) == 1


def test_snapshot_regeneration_collision_exhaustion_preserves_leaf(
    tmp_path, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / "first.txt"
    snapshot.write_text("ORIGINAL\n", encoding="utf-8")
    staged = snapshot_dir / ".chordatlas-snapshot-fixed.tmp"
    staged.write_text("OCCUPIED\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda count: "fixed")
    monkeypatch.setattr(
        snapshots, "_snapshot_files", lambda root, target: _stub_snapshot_files("first.txt")
    )

    with pytest.raises(OSError, match="unable to allocate a private staging file"):
        snapshots.regenerate_snapshots(tmp_path)

    assert snapshot.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert staged.read_text(encoding="utf-8") == "OCCUPIED\n"


def test_snapshots_regenerate_contains_source_and_write_failures(
    tmp_path, capsys, monkeypatch
) -> None:
    missing_root = tmp_path / "missing-source"
    (missing_root / "tests" / "snapshots").mkdir(parents=True)
    monkeypatch.chdir(missing_root)

    assert main(["snapshots", "regenerate", "open-string"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot regeneration failed; snapshots may be partially updated:" in captured.err

    blocked_root = tmp_path / "blocked-write"
    blocked_root.mkdir()
    workspace = _copy_snapshot_workspace(blocked_root)
    snapshot_dir = workspace / "tests" / "snapshots"
    shutil.rmtree(snapshot_dir)
    snapshot_dir.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert main(["snapshots", "regenerate", "open-string"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Snapshot regeneration failed; snapshots may be partially updated:" in captured.err
    assert "Regenerated snapshots:" not in captured.err


def test_snapshot_regeneration_materializes_all_outputs_before_writing(
    tmp_path, monkeypatch
) -> None:
    snapshot_dir = tmp_path / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    first = snapshot_dir / "first.txt"
    second = snapshot_dir / "second.txt"
    first.write_text("ORIGINAL FIRST\n", encoding="utf-8")
    second.write_text("ORIGINAL SECOND\n", encoding="utf-8")
    original = {
        path.name: (path.read_bytes(), path.stat().st_ino) for path in (first, second)
    }

    def fail_second() -> str:
        raise ValueError("second render failed")

    monkeypatch.setattr(
        snapshots,
        "_snapshot_files",
        lambda root, target: (
            snapshots.SnapshotFile("first.txt", lambda: "NEW FIRST\n"),
            snapshots.SnapshotFile("second.txt", fail_second),
        ),
    )

    with pytest.raises(ValueError, match="second render failed"):
        snapshots.regenerate_snapshots(tmp_path)

    for path in (first, second):
        assert (path.read_bytes(), path.stat().st_ino) == original[path.name]


def test_snapshot_regeneration_render_interrupt_does_not_create_output_directory(
    tmp_path, monkeypatch
) -> None:
    def interrupt() -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        snapshots,
        "_snapshot_files",
        lambda root, target: (snapshots.SnapshotFile("first.txt", interrupt),),
    )

    with pytest.raises(KeyboardInterrupt):
        snapshots.regenerate_snapshots(tmp_path)

    assert not (tmp_path / "tests" / "snapshots").exists()


def test_snapshot_regeneration_renders_each_file_once_in_registered_order(
    tmp_path, monkeypatch
) -> None:
    calls = []

    def render(name: str) -> str:
        calls.append(name)
        return f"{name}\n"

    monkeypatch.setattr(
        snapshots,
        "_snapshot_files",
        lambda root, target: tuple(
            snapshots.SnapshotFile(name, lambda name=name: render(name))
            for name in ("first.txt", "second.txt", "third.txt")
        ),
    )

    result = snapshots.regenerate_snapshots(tmp_path)

    assert calls == ["first.txt", "second.txt", "third.txt"]
    assert result.checked == ("first.txt", "second.txt", "third.txt")
    assert result.written == result.checked
    for name in result.written:
        assert (tmp_path / "tests" / "snapshots" / name).read_text(encoding="utf-8") == (
            f"{name}\n"
        )


def test_snapshots_rejects_unknown_target(capsys) -> None:
    assert main(["snapshots", "check", "missing"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unknown snapshot target: missing" in captured.err


def test_release_check_command_delegates(capsys, monkeypatch) -> None:
    monkeypatch.setattr("chordatlas.cli.run_release_check", lambda *, stderr: 0)

    assert main(["release-check"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""


def test_release_check_command_reports_delegate_failure(capsys, monkeypatch) -> None:
    monkeypatch.setattr("chordatlas.cli.run_release_check", lambda *, stderr: 1)

    assert main(["release-check"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
