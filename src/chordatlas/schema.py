from __future__ import annotations

import importlib
import importlib.resources
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from chordatlas.models import SongChart

SCHEMA_PACKAGE = "chordatlas.schemas"
SONG_SCHEMA = "song-chart.schema.json"
PROVENANCE_SCHEMA = "provenance-record.schema.json"
RECORDING_COMPARISON_SCHEMA = "recording-comparison.schema.json"
RECORDING_COMPARISON_METADATA_SCHEMA = "recording-comparison-metadata.schema.json"
SCHEMA_NAMES = (
    SONG_SCHEMA,
    PROVENANCE_SCHEMA,
    RECORDING_COMPARISON_SCHEMA,
    RECORDING_COMPARISON_METADATA_SCHEMA,
)


class SchemaSource(Protocol):
    def joinpath(self, child: str) -> Any:
        """Return a child schema resource."""



@dataclass(frozen=True)
class SchemaValidationResult:
    """Result of optional normalized JSON Schema validation."""

    status: str
    errors: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def failed(self) -> bool:
        return self.status == "failed"


@dataclass(frozen=True)
class SchemaMirrorResult:
    """Result of checking or syncing the source-tree schema mirror."""

    status: str
    mirror_dir: Path
    drift: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return self.status == "clean"

    @property
    def synced(self) -> bool:
        return self.status == "synced"

    @property
    def dirty(self) -> bool:
        return self.status == "dirty"


def validate_chart_schema(chart: SongChart) -> SchemaValidationResult:
    """Validate a chart's normalized JSON export when schema tooling is available."""

    try:
        jsonschema = importlib.import_module("jsonschema")
        referencing = importlib.import_module("referencing")
    except ImportError:
        return SchemaValidationResult(
            status="skipped",
            reason="jsonschema is not installed",
        )

    try:
        song_schema, provenance_schema = _load_schemas()
    except OSError as error:
        return SchemaValidationResult(
            status="skipped",
            reason=f"schema files are unavailable: {error}",
        )

    registry = referencing.Registry().with_resource(
        provenance_schema["$id"],
        referencing.Resource.from_contents(provenance_schema),
    )
    validator = jsonschema.Draft202012Validator(song_schema, registry=registry)
    errors = tuple(
        _format_error(error)
        for error in sorted(validator.iter_errors(chart.to_mapping()), key=str)
    )
    if errors:
        return SchemaValidationResult(status="failed", errors=errors)
    return SchemaValidationResult(status="passed")


def check_schema_mirror(mirror_dir: Path | None = None) -> SchemaMirrorResult:
    """Check whether the source-tree schema mirror matches packaged schemas."""

    target = _mirror_dir(mirror_dir)
    drift = tuple(name for name in SCHEMA_NAMES if _mirror_text(target, name) != _schema_text(name))
    status = "dirty" if drift else "clean"
    return SchemaMirrorResult(status=status, mirror_dir=target, drift=drift)


def sync_schema_mirror(mirror_dir: Path | None = None) -> SchemaMirrorResult:
    """Copy canonical packaged schemas into the source-tree mirror."""

    target = _mirror_dir(mirror_dir)
    before = check_schema_mirror(target).drift
    target.mkdir(parents=True, exist_ok=True)
    for name in SCHEMA_NAMES:
        (target / name).write_text(_schema_text(name), encoding="utf-8")
    return SchemaMirrorResult(status="synced", mirror_dir=target, drift=before)


def _load_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    schema_dir = _schema_source()
    return (
        _read_json(schema_dir.joinpath(SONG_SCHEMA)),
        _read_json(schema_dir.joinpath(PROVENANCE_SCHEMA)),
    )


def _schema_source() -> SchemaSource:
    try:
        packaged = importlib.resources.files(SCHEMA_PACKAGE)
        if packaged.joinpath(SONG_SCHEMA).is_file():
            return packaged
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    candidates = [
        Path.cwd() / "schemas",
        Path(__file__).resolve().parents[2] / "schemas",
    ]
    for candidate in candidates:
        if (candidate / "song-chart.schema.json").exists():
            return candidate
    return candidates[0]


def _schema_text(name: str) -> str:
    return importlib.resources.files(SCHEMA_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def _mirror_dir(mirror_dir: Path | None) -> Path:
    if mirror_dir is not None:
        return mirror_dir
    cwd_schema = Path.cwd() / "schemas"
    if cwd_schema.exists():
        return cwd_schema
    return Path(__file__).resolve().parents[2] / "schemas"


def _mirror_text(mirror_dir: Path, name: str) -> str | None:
    path = mirror_dir / name
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _read_json(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    if not location:
        location = "<root>"
    return f"{location}: {error.message}"
