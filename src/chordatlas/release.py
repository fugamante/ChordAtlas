from __future__ import annotations

import compileall
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import TextIO

from chordatlas.schema import SCHEMA_NAMES, check_schema_mirror
from chordatlas.snapshots import check_snapshots


def run_release_check(root: Path | None = None, *, stderr: TextIO = sys.stderr) -> int:
    """Run the complete non-interactive pre-release validation stack."""

    source_root = root or Path.cwd()
    if not _is_source_root(source_root):
        print(
            f"Release check must run from the ChordAtlas source root: {source_root}",
            file=stderr,
        )
        return 1

    steps = (
        ("schema mirror check", lambda: _check_schema_mirror(source_root, stderr)),
        ("snapshot verification", lambda: _verify_snapshots(source_root, stderr)),
        ("compileall", lambda: _run_compileall(source_root, stderr)),
        ("pytest", lambda: _run_pytest(source_root, stderr)),
        ("packaging build inspection", lambda: _inspect_package_build(source_root, stderr)),
    )

    for name, step in steps:
        print(f"[release-check] {name}", file=stderr)
        if step() != 0:
            print(f"[release-check] failed: {name}", file=stderr)
            _cleanup_caches(source_root)
            return 1

    _cleanup_caches(source_root)
    print("[release-check] passed", file=stderr)
    return 0


def _is_source_root(path: Path) -> bool:
    return (path / "pyproject.toml").exists() and (path / "src" / "chordatlas").is_dir()


def _check_schema_mirror(root: Path, stderr: TextIO) -> int:
    result = check_schema_mirror(root / "schemas")
    if result.clean:
        return 0
    print(f"Schema mirror drift detected: {result.mirror_dir}", file=stderr)
    for name in result.drift:
        print(f"- {name}", file=stderr)
    print("Run `chordchart schemas --sync` before release.", file=stderr)
    return 1


def _verify_snapshots(root: Path, stderr: TextIO) -> int:
    result = check_snapshots(root)
    if result.clean:
        return 0
    print("Snapshot drift detected:", file=stderr)
    for name in result.drift:
        print(f"- {name}", file=stderr)
    print("Regenerate snapshots from current renderers before release.", file=stderr)
    return 1


def _run_compileall(root: Path, stderr: TextIO) -> int:
    ok = compileall.compile_dir(root / "src", quiet=1) and compileall.compile_dir(
        root / "tests",
        quiet=1,
    )
    _cleanup_caches(root)
    if ok:
        return 0
    print("Python compilation failed under src/ or tests/.", file=stderr)
    return 1


def _run_pytest(root: Path, stderr: TextIO) -> int:
    return _run_command([sys.executable, "-m", "pytest"], cwd=root, stderr=stderr)


def _inspect_package_build(root: Path, stderr: TextIO) -> int:
    with tempfile.TemporaryDirectory(prefix="chordatlas-release-") as tmp:
        out_dir = Path(tmp) / "dist"
        if (
            _run_command(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--sdist",
                    "--outdir",
                    str(out_dir),
                ],
                cwd=root,
                stderr=stderr,
            )
            != 0
        ):
            return 1

        try:
            wheel_path = next(out_dir.glob("chordatlas-*.whl"))
            sdist_path = next(out_dir.glob("chordatlas-*.tar.gz"))
        except StopIteration:
            print("Package build did not produce both wheel and sdist artifacts.", file=stderr)
            return 1

        if not _wheel_has_schemas(wheel_path):
            print(f"Wheel is missing packaged schema resources: {wheel_path}", file=stderr)
            return 1
        if not _sdist_has_schemas(sdist_path):
            print(f"sdist is missing schema sources or mirrors: {sdist_path}", file=stderr)
            return 1
    return 0


def _wheel_has_schemas(path: Path) -> bool:
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
    return all(f"chordatlas/schemas/{name}" in names for name in SCHEMA_NAMES)


def _sdist_has_schemas(path: Path) -> bool:
    with tarfile.open(path) as sdist:
        names = set(sdist.getnames())
    for schema_name in SCHEMA_NAMES:
        has_mirror = any(
            name.endswith(f"/schemas/{schema_name}") and "/src/" not in name for name in names
        )
        has_package = any(name.endswith(f"/src/chordatlas/schemas/{schema_name}") for name in names)
        if not has_mirror or not has_package:
            return False
    return True


def _run_command(command: list[str], *, cwd: Path, stderr: TextIO) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return 0
    print(f"Command failed: {' '.join(command)}", file=stderr)
    if result.stdout:
        print(result.stdout, file=stderr)
    if result.stderr:
        print(result.stderr, file=stderr)
    return result.returncode


def _cleanup_caches(root: Path) -> None:
    for base in (root / "src", root / "tests"):
        if not base.exists():
            continue
        for cache in base.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
