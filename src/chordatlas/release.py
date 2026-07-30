from __future__ import annotations

import compileall
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
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
        try:
            result = step()
        except Exception as exc:
            print(
                f"[release-check] error: {name}: {type(exc).__name__}: {exc}",
                file=stderr,
            )
            for note in getattr(exc, "__notes__", ()):
                print(f"[release-check] note: {note}", file=stderr)
            print(f"[release-check] failed: {name}", file=stderr)
            return 1
        if result != 0:
            print(f"[release-check] failed: {name}", file=stderr)
            return 1

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
    previous_prefix = sys.pycache_prefix
    with _temporary_directory(prefix="chordatlas-compile-") as tmp:
        try:
            sys.pycache_prefix = tmp
            ok = compileall.compile_dir(root / "src", quiet=1) and compileall.compile_dir(
                root / "tests",
                quiet=1,
            )
        finally:
            sys.pycache_prefix = previous_prefix
        if not ok:
            print("Python compilation failed under src/ or tests/.", file=stderr)
            return 1
    return 0


def _run_pytest(root: Path, stderr: TextIO) -> int:
    return _run_command(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=root,
        stderr=stderr,
        env_overrides={
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "",
        },
    )


def _inspect_package_build(root: Path, stderr: TextIO) -> int:
    with _temporary_directory(prefix="chordatlas-release-") as tmp:
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


@contextmanager
def _temporary_directory(*, prefix: str) -> Iterator[str]:
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    active_error: BaseException | None = None
    try:
        yield temporary.name
    except BaseException as error:
        active_error = error
        raise
    finally:
        try:
            temporary.cleanup()
        except OSError as cleanup_error:
            message = (
                "Temporary cleanup failed: temporary directory "
                f"{temporary.name}: {cleanup_error}"
            )
            if active_error is not None:
                active_error.add_note(message)
            else:
                raise OSError(message) from cleanup_error


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


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    stderr: TextIO,
    env_overrides: dict[str, str] | None = None,
) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_overrides:
        env.update(env_overrides)
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
