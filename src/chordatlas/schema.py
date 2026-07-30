from __future__ import annotations

import importlib
import importlib.resources
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from chordatlas._fs import replace_text_batch
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

    mapping = chart.to_mapping()
    try:
        if _has_non_string_json_key(mapping):
            return SchemaValidationResult(
                status="failed",
                errors=("<root>: normalized chart JSON object keys must be strings",),
            )
        serialized = json.dumps(mapping, sort_keys=True, allow_nan=False)
        instance = json.loads(serialized)
    except TypeError:
        return SchemaValidationResult(
            status="failed",
            errors=("<root>: normalized chart contains a value that is not JSON serializable",),
        )
    except ValueError:
        return SchemaValidationResult(
            status="failed",
            errors=("<root>: normalized chart is not strict JSON",),
        )
    except RecursionError:
        return SchemaValidationResult(
            status="failed",
            errors=("<root>: normalized chart exceeds JSON nesting limits",),
        )

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
        for error in sorted(validator.iter_errors(instance), key=str)
    )
    if errors:
        return SchemaValidationResult(status="failed", errors=errors)
    return SchemaValidationResult(status="passed")


def _has_non_string_json_key(value: Any, active: set[int] | None = None) -> bool:
    """Return whether a JSON-shaped value contains an object key that is not text."""

    if not isinstance(value, (dict, list, tuple)):
        return False

    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        return False

    active.add(identity)
    try:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                return True
            children = value.values()
        else:
            children = value
        return any(_has_non_string_json_key(child, active) for child in children)
    finally:
        active.remove(identity)


def check_schema_mirror(mirror_dir: Path | None = None) -> SchemaMirrorResult:
    """Check whether the source-tree schema mirror matches packaged schemas."""

    target = _mirror_dir(mirror_dir)
    canonical = _canonical_schema_texts()
    drift = tuple(
        name for name in SCHEMA_NAMES if _mirror_text(target, name) != canonical[name]
    )
    status = "dirty" if drift else "clean"
    return SchemaMirrorResult(status=status, mirror_dir=target, drift=drift)


def sync_schema_mirror(mirror_dir: Path | None = None) -> SchemaMirrorResult:
    """Copy canonical packaged schemas into the source-tree mirror."""

    target = _mirror_dir(mirror_dir)
    canonical = _canonical_schema_texts()
    before = tuple(
        name for name in SCHEMA_NAMES if _mirror_text(target, name) != canonical[name]
    )
    target.mkdir(parents=True, exist_ok=True)
    replace_text_batch(
        target,
        ((name, canonical[name]) for name in SCHEMA_NAMES),
        stage_prefix=".chordatlas-schema-",
    )
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


def _canonical_schema_texts() -> dict[str, str]:
    return {name: _schema_text(name) for name in SCHEMA_NAMES}


def _mirror_dir(mirror_dir: Path | None) -> Path:
    if mirror_dir is not None:
        return mirror_dir
    cwd_schema = Path.cwd() / "schemas"
    if cwd_schema.exists():
        return cwd_schema
    return Path(__file__).resolve().parents[2] / "schemas"


def _mirror_text(mirror_dir: Path, name: str) -> str | None:
    path = mirror_dir / name
    if path.is_symlink():
        return None
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError:
        return None


def _read_json(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    if not location:
        location = "<root>"
    return f"{location}: {error.message}"
