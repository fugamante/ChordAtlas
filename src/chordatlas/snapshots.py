from __future__ import annotations

import difflib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TextIO

from chordatlas._fs import PublishedCleanupError, replace_text_batch
from chordatlas.compare import (
    ComparisonFilters,
    comparison_metadata_to_json,
    comparison_to_csv,
    comparison_to_json,
)
from chordatlas.io import load_song_chart, song_chart_to_json
from chordatlas.render import render_markdown, render_text

SNAPSHOT_TARGETS = ("open-string", "research-comparison")
MAX_INLINE_DIFF_LINES = 80


class DiffArtifactPolicyError(OSError):
    """An existing user-selected artifact leaf is unsafe to overwrite."""


class DiffArtifactsPublishedCleanupError(PublishedCleanupError):
    """All planned diff artifacts were published before cleanup failed."""

    def __init__(self, directory: Path, message: str) -> None:
        super().__init__(message)
        self.directory = directory


class DiffArtifactsNotPublishedError(OSError):
    """Diff artifact publication failed before the first leaf was replaced."""


@dataclass(frozen=True)
class SnapshotFile:
    name: str
    render: Callable[[], str]


@dataclass(frozen=True)
class SnapshotResult:
    status: str
    root: Path
    target: str
    checked: tuple[str, ...]
    drift: tuple[str, ...] = ()
    written: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return self.status == "clean"

    @property
    def dirty(self) -> bool:
        return self.status == "dirty"

    @property
    def regenerated(self) -> bool:
        return self.status == "regenerated"


def available_snapshot_targets() -> tuple[str, ...]:
    return SNAPSHOT_TARGETS


def check_snapshots(root: Path | None = None, *, target: str = "all") -> SnapshotResult:
    source_root = root or Path.cwd()
    files = _snapshot_files(source_root, target)
    drift = []
    for file in files:
        expected = _read_snapshot(source_root, file.name)
        if expected is None or expected != file.render():
            drift.append(file.name)
    return SnapshotResult(
        status="dirty" if drift else "clean",
        root=source_root,
        target=target,
        checked=tuple(file.name for file in files),
        drift=tuple(drift),
    )


def regenerate_snapshots(root: Path | None = None, *, target: str = "all") -> SnapshotResult:
    source_root = root or Path.cwd()
    files = _snapshot_files(source_root, target)
    rendered = tuple((file.name, file.render()) for file in files)
    snapshot_dir = source_root / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    replace_text_batch(
        snapshot_dir,
        rendered,
        stage_prefix=".chordatlas-snapshot-",
    )
    written = tuple(name for name, _ in rendered)
    return SnapshotResult(
        status="regenerated",
        root=source_root,
        target=target,
        checked=tuple(file.name for file in files),
        written=written,
    )


def run_snapshot_check(
    root: Path | None = None,
    *,
    target: str = "all",
    diff_dir: Path | None = None,
    output_format: Literal["text", "json"] = "text",
    stdout: TextIO | None = None,
    stderr: TextIO,
) -> int:
    source_root = root or Path.cwd()
    try:
        _validate_snapshot_target(target)
    except ValueError as error:
        print(error, file=stderr)
        return 2
    if output_format != "text":
        if output_format != "json":
            print(f"Unsupported snapshot check format: {output_format}", file=stderr)
            return 2
    try:
        result = check_snapshots(source_root, target=target)
        if result.clean:
            if output_format == "json":
                payload = _snapshot_check_payload(result, (), {})
                print(
                    json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
                    file=stdout or stderr,
                )
                return 0
            print(f"Snapshots are in sync: {target}", file=stderr)
            return 0

        diffs = _prepare_snapshot_diffs(source_root, result)
        artifact_paths: dict[str, str] = {}
        selected_diff_dir: Path | None = None
        if output_format == "text":
            _print_snapshot_drift(target, diffs, stderr=stderr)
        if diff_dir is not None:
            try:
                selected_diff_dir, artifact_paths = _publish_diff_artifacts(
                    diff_dir, diffs
                )
            except DiffArtifactsPublishedCleanupError as error:
                print(
                    "Snapshot check failed: diff artifacts were written to "
                    f"{error.directory}, but temporary cleanup failed: {error}",
                    file=stderr,
                )
                for note in getattr(error, "__notes__", ()):
                    print(note, file=stderr)
                return 1
            except DiffArtifactPolicyError as error:
                print(
                    f"Snapshot check failed: {error}; no diff artifacts were written.",
                    file=stderr,
                )
                for note in getattr(error, "__notes__", ()):
                    print(note, file=stderr)
                return 1
            except DiffArtifactsNotPublishedError as error:
                print(
                    f"Snapshot check failed: no diff artifacts were written: {error}",
                    file=stderr,
                )
                for note in getattr(error, "__notes__", ()):
                    print(note, file=stderr)
                return 1
            except OSError as error:
                print(
                    "Snapshot check failed: diff artifacts may be partially updated: "
                    f"{error}",
                    file=stderr,
                )
                for note in getattr(error, "__notes__", ()):
                    print(note, file=stderr)
                return 1

        if output_format == "json":
            payload = _snapshot_check_payload(result, diffs, artifact_paths)
            print(
                json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
                file=stdout or stderr,
            )
            return 1
        print("Run `chordchart snapshots regenerate` to update snapshots.", file=stderr)
        if selected_diff_dir is not None:
            print(f"Full diffs written to: {selected_diff_dir}", file=stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Snapshot check failed: {error}", file=stderr)
        return 1


def run_snapshot_regenerate(
    root: Path | None = None,
    *,
    target: str = "all",
    stderr: TextIO,
) -> int:
    try:
        _validate_snapshot_target(target)
    except ValueError as error:
        print(error, file=stderr)
        return 2
    try:
        result = regenerate_snapshots(root, target=target)
    except PublishedCleanupError as error:
        print(
            f"Snapshots regenerated, but temporary cleanup failed: {error}",
            file=stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(
            f"Snapshot regeneration failed; snapshots may be partially updated: {error}",
            file=stderr,
        )
        for note in getattr(error, "__notes__", ()):
            print(note, file=stderr)
        return 1
    print(f"Regenerated snapshots: {target}", file=stderr)
    for name in result.written:
        print(f"- {name}", file=stderr)
    return 0


def _snapshot_files(root: Path, target: str) -> tuple[SnapshotFile, ...]:
    if target == "all":
        files: list[SnapshotFile] = []
        for name in SNAPSHOT_TARGETS:
            files.extend(_snapshot_files(root, name))
        return tuple(files)
    if target == "open-string":
        return _open_string_snapshots(root)
    if target == "research-comparison":
        return _research_comparison_snapshots(root)
    _validate_snapshot_target(target)
    raise AssertionError(f"Unhandled snapshot target: {target}")


def _validate_snapshot_target(target: str) -> None:
    if target in ("all",) + SNAPSHOT_TARGETS:
        return
    allowed = ", ".join(("all",) + SNAPSHOT_TARGETS)
    raise ValueError(f"Unknown snapshot target: {target}. Expected one of: {allowed}")


def _open_string_snapshots(root: Path) -> tuple[SnapshotFile, ...]:
    chart = load_song_chart(root / "examples" / "open-string-progression.yaml")
    return (
        SnapshotFile("open-string-progression.md", lambda: render_markdown(chart)),
        SnapshotFile("open-string-progression.txt", lambda: render_text(chart)),
        SnapshotFile("open-string-progression.json", lambda: song_chart_to_json(chart)),
    )


def _research_comparison_snapshots(root: Path) -> tuple[SnapshotFile, ...]:
    chart = load_song_chart(root / "examples" / "research-recording-comparison.yaml")
    filters = ComparisonFilters.from_values(
        categories=["effects"],
        recording_ids=["remaster"],
        severities=["medium"],
        source_specific_only=True,
    )
    return (
        SnapshotFile(
            "research-recording-comparison.effects-remaster.csv",
            lambda: comparison_to_csv(chart, filters=filters, provenance_mode="research"),
        ),
        SnapshotFile(
            "research-recording-comparison.effects-remaster.json",
            lambda: comparison_to_json(chart, filters=filters, provenance_mode="research"),
        ),
        SnapshotFile(
            "research-recording-comparison.effects-remaster.metadata.json",
            lambda: comparison_metadata_to_json(
                chart,
                filters=filters,
                provenance_mode="research",
            ),
        ),
    )


def _snapshot_path(root: Path, name: str) -> Path:
    return root / "tests" / "snapshots" / name


def _snapshot_diff(root: Path, name: str) -> list[str]:
    expected = _read_snapshot(root, name)
    if expected is None:
        expected = ""
    rendered = _render_snapshot(root, name)
    return list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"snapshot/{name}",
            tofile=f"generated/{name}",
        )
    )


def _read_snapshot(root: Path, name: str) -> str | None:
    path = _snapshot_path(root, name)
    if path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _render_snapshot(root: Path, name: str) -> str:
    for file in _snapshot_files(root, "all"):
        if file.name == name:
            return file.render()
    raise ValueError(f"Unknown snapshot file: {name}")


def _print_bounded_diff(diff_lines: tuple[str, ...], *, stderr: TextIO) -> None:
    if not diff_lines:
        return
    shown = diff_lines[:MAX_INLINE_DIFF_LINES]
    for line in shown:
        print(line.rstrip("\n"), file=stderr)
    if len(diff_lines) > MAX_INLINE_DIFF_LINES:
        omitted = len(diff_lines) - MAX_INLINE_DIFF_LINES
        print(f"... diff truncated, {omitted} line(s) omitted", file=stderr)


def _print_snapshot_drift(
    target: str,
    diffs: tuple[tuple[str, str, tuple[str, ...]], ...],
    *,
    stderr: TextIO,
) -> None:
    print(f"Snapshot drift detected: {target}", file=stderr)
    for name, _, diff_lines in diffs:
        print(f"- {name}", file=stderr)
        _print_bounded_diff(diff_lines, stderr=stderr)


def _prepare_snapshot_diffs(
    root: Path,
    result: SnapshotResult,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    prepared = []
    leaves = set()
    for name in result.drift:
        if (
            not name
            or "\0" in name
            or name in {".", ".."}
            or Path(name).name != name
        ):
            raise ValueError(f"Invalid snapshot artifact name: {name!r}")
        leaf = f"{name}.diff"
        if leaf in leaves:
            raise ValueError(f"Duplicate snapshot artifact name: {leaf}")
        leaves.add(leaf)
        prepared.append((name, leaf, tuple(_snapshot_diff(root, name))))
    return tuple(prepared)


def _publish_diff_artifacts(
    diff_dir: Path,
    diffs: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> tuple[Path, dict[str, str]]:
    """Atomically publish diffs over missing or single-link regular leaves only."""

    try:
        diff_dir.mkdir(parents=True, exist_ok=True)
        selected_dir = diff_dir.resolve(strict=True)
    except OSError as error:
        raise DiffArtifactsNotPublishedError(str(error)) from error

    def guard(leaf: str, target: os.stat_result | None) -> None:
        _guard_diff_artifact(selected_dir, leaf, target)

    published = []
    try:
        replace_text_batch(
            selected_dir,
            ((leaf, "".join(lines)) for _, leaf, lines in diffs),
            stage_prefix=".chordatlas-diff-",
            leaf_guard=guard,
            on_published=published.append,
        )
    except PublishedCleanupError as error:
        published_error = DiffArtifactsPublishedCleanupError(
            selected_dir,
            str(error),
        )
        for note in getattr(error, "__notes__", ()):
            published_error.add_note(note)
        raise published_error from error
    except DiffArtifactPolicyError:
        raise
    except OSError as error:
        if published:
            raise
        not_published = DiffArtifactsNotPublishedError(str(error))
        for note in getattr(error, "__notes__", ()):
            not_published.add_note(note)
        raise not_published from error
    return selected_dir, {
        name: str(selected_dir / leaf) for name, leaf, _ in diffs
    }


def _guard_diff_artifact(
    parent: Path,
    leaf: str,
    target: os.stat_result | None,
) -> None:
    if target is None:
        return
    if stat.S_ISREG(target.st_mode) and target.st_nlink == 1:
        return
    if stat.S_ISLNK(target.st_mode):
        kind = "a symbolic link"
    elif stat.S_ISDIR(target.st_mode):
        kind = "a directory"
    elif stat.S_ISREG(target.st_mode):
        kind = f"a regular file with {target.st_nlink} hard links"
    else:
        kind = "a special file"
    raise DiffArtifactPolicyError(
        f"Refusing diff artifact {parent / leaf}: destination is {kind}"
    )


def _snapshot_check_payload(
    result: SnapshotResult,
    diffs: tuple[tuple[str, str, tuple[str, ...]], ...],
    artifact_paths: dict[str, str],
) -> dict[str, object]:
    payload_diffs = []
    for name, _, diff_lines in diffs:
        payload_diffs.append(
            {
                "file": name,
                "truncated": len(diff_lines) > MAX_INLINE_DIFF_LINES,
                "inline_line_count": min(len(diff_lines), MAX_INLINE_DIFF_LINES),
                "total_line_count": len(diff_lines),
                "diff_artifact": artifact_paths.get(name),
            }
        )
    return {
        "status": result.status,
        "target": result.target,
        "checked_files": list(result.checked),
        "drift_files": list(result.drift),
        "diffs": payload_diffs,
    }
