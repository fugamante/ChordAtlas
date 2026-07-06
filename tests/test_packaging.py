import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_NAMES = (
    "song-chart.schema.json",
    "provenance-record.schema.json",
    "recording-comparison.schema.json",
    "recording-comparison-metadata.schema.json",
)


@pytest.fixture(scope="session")
def built_dists(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out_dir = tmp_path_factory.mktemp("dists")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(out_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        next(out_dir.glob("chordatlas-*.whl")),
        next(out_dir.glob("chordatlas-*.tar.gz")),
    )


def test_packaged_schema_resources_match_repo_schemas() -> None:
    import importlib.resources

    packaged = importlib.resources.files("chordatlas.schemas")
    for schema_name in SCHEMA_NAMES:
        repo_schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        package_schema = json.loads(packaged.joinpath(schema_name).read_text(encoding="utf-8"))

        assert package_schema == repo_schema


def test_wheel_contains_packaged_schema_resources(built_dists: tuple[Path, Path]) -> None:
    wheel_path, _sdist_path = built_dists

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    for schema_name in SCHEMA_NAMES:
        assert f"chordatlas/schemas/{schema_name}" in names


def test_sdist_contains_repo_and_packaged_schemas(built_dists: tuple[Path, Path]) -> None:
    _wheel_path, sdist_path = built_dists

    with tarfile.open(sdist_path) as sdist:
        names = set(sdist.getnames())

    for schema_name in SCHEMA_NAMES:
        assert any(
            name.endswith(f"/schemas/{schema_name}") and "/src/" not in name for name in names
        )
        assert any(name.endswith(f"/src/chordatlas/schemas/{schema_name}") for name in names)


def test_installed_wheel_validate_works_outside_repo(
    built_dists: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    wheel_path, _sdist_path = built_dists
    target = tmp_path / "site"
    chart_path = tmp_path / "chart.yaml"
    chart_path.write_text("title: Installed Package Test\nsections: []\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(wheel_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(target)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from chordatlas.cli import main; "
                "raise SystemExit(main(['validate', 'chart.yaml']))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Valid chart:" in result.stderr
    assert "Schema validation skipped" not in result.stderr
    assert result.stdout == ""
