from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TextIO

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
    drift = tuple(
        file.name
        for file in files
        if _snapshot_path(source_root, file.name).read_text(encoding="utf-8") != file.render()
    )
    return SnapshotResult(
        status="dirty" if drift else "clean",
        root=source_root,
        target=target,
        checked=tuple(file.name for file in files),
        drift=drift,
    )


def regenerate_snapshots(root: Path | None = None, *, target: str = "all") -> SnapshotResult:
    source_root = root or Path.cwd()
    files = _snapshot_files(source_root, target)
    snapshot_dir = source_root / "tests" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for file in files:
        _snapshot_path(source_root, file.name).write_text(file.render(), encoding="utf-8")
        written.append(file.name)
    return SnapshotResult(
        status="regenerated",
        root=source_root,
        target=target,
        checked=tuple(file.name for file in files),
        written=tuple(written),
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
        result = check_snapshots(source_root, target=target)
    except ValueError as error:
        print(error, file=stderr)
        return 2
    if output_format == "json":
        payload = _snapshot_check_payload(source_root, result, diff_dir=diff_dir)
        print(json.dumps(payload, indent=2, sort_keys=True), file=stdout or stderr)
        return 0 if result.clean else 1
    if output_format != "text":
        print(f"Unsupported snapshot check format: {output_format}", file=stderr)
        return 2
    if result.clean:
        print(f"Snapshots are in sync: {target}", file=stderr)
        return 0
    print(f"Snapshot drift detected: {target}", file=stderr)
    for name in result.drift:
        print(f"- {name}", file=stderr)
        diff_lines = _snapshot_diff(source_root, name)
        _print_bounded_diff(diff_lines, stderr=stderr)
        if diff_dir is not None:
            _write_diff_artifact(diff_dir, name, diff_lines)
    print("Run `chordchart snapshots regenerate` to update snapshots.", file=stderr)
    if diff_dir is not None:
        print(f"Full diffs written to: {diff_dir}", file=stderr)
    return 1


def run_snapshot_regenerate(
    root: Path | None = None,
    *,
    target: str = "all",
    stderr: TextIO,
) -> int:
    try:
        result = regenerate_snapshots(root, target=target)
    except ValueError as error:
        print(error, file=stderr)
        return 2
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
    snapshot = _snapshot_path(root, name)
    expected = snapshot.read_text(encoding="utf-8")
    rendered = _render_snapshot(root, name)
    return list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"snapshot/{name}",
            tofile=f"generated/{name}",
        )
    )


def _render_snapshot(root: Path, name: str) -> str:
    for file in _snapshot_files(root, "all"):
        if file.name == name:
            return file.render()
    raise ValueError(f"Unknown snapshot file: {name}")


def _print_bounded_diff(diff_lines: list[str], *, stderr: TextIO) -> None:
    if not diff_lines:
        return
    shown = diff_lines[:MAX_INLINE_DIFF_LINES]
    for line in shown:
        print(line.rstrip("\n"), file=stderr)
    if len(diff_lines) > MAX_INLINE_DIFF_LINES:
        omitted = len(diff_lines) - MAX_INLINE_DIFF_LINES
        print(f"... diff truncated, {omitted} line(s) omitted", file=stderr)


def _write_diff_artifact(diff_dir: Path, name: str, diff_lines: list[str]) -> None:
    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"{name}.diff"
    diff_path.write_text("".join(diff_lines), encoding="utf-8")


def _snapshot_check_payload(
    root: Path,
    result: SnapshotResult,
    *,
    diff_dir: Path | None,
) -> dict[str, object]:
    diffs = []
    for name in result.drift:
        diff_lines = _snapshot_diff(root, name)
        artifact_path = None
        if diff_dir is not None:
            _write_diff_artifact(diff_dir, name, diff_lines)
            artifact_path = str((diff_dir / f"{name}.diff").resolve())
        diffs.append(
            {
                "file": name,
                "truncated": len(diff_lines) > MAX_INLINE_DIFF_LINES,
                "inline_line_count": min(len(diff_lines), MAX_INLINE_DIFF_LINES),
                "total_line_count": len(diff_lines),
                "diff_artifact": artifact_path,
            }
        )
    return {
        "status": result.status,
        "target": result.target,
        "checked_files": list(result.checked),
        "drift_files": list(result.drift),
        "diffs": diffs,
    }
