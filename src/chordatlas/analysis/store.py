from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chordatlas._fs import (
    ProjectAnchor,
    TargetOccupiedError,
    create_text_exclusive,
    replace_text,
)
from chordatlas.analysis.models import (
    AnalysisError,
    AnalysisRun,
    AnalysisSpec,
    ChordCandidateTimeline,
    RunState,
    VALID_TRANSITIONS,
    canonical_json,
    config_from_mapping,
    run_from_mapping,
    spec_from_mapping,
    state_from_mapping,
    timeline_from_mapping,
)

_MAX_JSON_BYTES = 8 * 1024 * 1024


class AnalysisStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        try:
            self.anchor = ProjectAnchor.for_project(self.project_root)
        except OSError:
            raise _integrity_error() from None
        self.root = self.anchor.root / "analysis"
        self.specs = self.root / "specs" / "sha256"
        self.runs = self.root / "runs"
        self.timelines = self.root / "timelines" / "sha256"
        self.claims = self.root / "claims"
        self.tmp = self.root / "tmp"
        self._lock = threading.RLock()
        self._claim_handles: dict[str, Any] = {}

    @classmethod
    def initialize(cls, project_root: Path) -> AnalysisStore:
        store = cls(project_root)
        for path in (
            store.root,
            store.root / "specs",
            store.specs,
            store.runs,
            store.root / "timelines",
            store.timelines,
            store.claims,
            store.tmp,
        ):
            _ensure_dir(path, store.anchor)
        return store

    def publish_spec(self, spec: AnalysisSpec) -> None:
        path = self._digest_path(self.specs, spec.id, create=True)
        self._publish_immutable(path, {"id": spec.id, **spec.identity_mapping()})

    def load_spec(self, spec_id: str) -> AnalysisSpec:
        try:
            spec = spec_from_mapping(
                self._read_json(self._digest_path(self.specs, spec_id))
            )
            if spec.id != spec_id:
                raise _integrity_error()
            return spec
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def create_run(
        self,
        spec: AnalysisSpec,
        *,
        source_id: str,
        retry_of: str | None = None,
    ) -> AnalysisRun:
        run = AnalysisRun.create(
            spec_id=spec.id,
            source_id=source_id,
            created_at=_utc_now(),
            retry_of=retry_of,
        )
        self._persist_run(spec, run)
        return run

    def create_claimed_run(
        self,
        spec: AnalysisSpec,
        *,
        source_id: str,
        retry_of: str | None = None,
    ) -> AnalysisRun:
        """Claim the project before making a nonterminal attempt visible."""

        run = AnalysisRun.create(
            spec_id=spec.id,
            source_id=source_id,
            created_at=_utc_now(),
            retry_of=retry_of,
        )
        self.claim(run.id)
        try:
            self._persist_run(spec, run)
        except Exception:
            self.release_claim(run.id)
            raise
        return run

    def _persist_run(self, spec: AnalysisSpec, run: AnalysisRun) -> None:
        if run.spec_id != spec.id:
            raise AnalysisError(
                "analysis_storage_integrity",
                "Private analysis storage failed an integrity check.",
                retryable=False,
            )
        self.publish_spec(spec)
        run_dir = self.runs / run.id
        _ensure_dir(run_dir, self.anchor)
        _ensure_dir(run_dir / "events", self.anchor)
        self._publish_immutable(run_dir / "request.json", run.to_record_mapping())
        state = RunState(run.id, 0, "queued", _utc_now())
        self._publish_immutable(run_dir / "events" / "00000000.json", state.to_record_mapping())
        self._replace_state(run_dir / "state.json", state)

    def load_run(self, run_id: str) -> AnalysisRun:
        _validate_run_id(run_id)
        try:
            run = run_from_mapping(
                self._read_json(self.runs / run_id / "request.json")
            )
            if run.id != run_id:
                raise _integrity_error()
            return run
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_state(self, run_id: str) -> RunState:
        _validate_run_id(run_id)
        run_dir = self.runs / run_id
        try:
            events = [
                run_dir / "events" / name
                for name in _json_names(run_dir / "events", self.anchor)
            ]
            if not events:
                raise _integrity_error()
            latest = state_from_mapping(self._read_json(events[-1]))
            if (
                latest.run_id != run_id
                or events[-1].name != f"{latest.revision:08d}.json"
            ):
                raise _integrity_error()
            try:
                cached = state_from_mapping(self._read_json(run_dir / "state.json"))
            except AnalysisError:
                self._replace_state(run_dir / "state.json", latest)
                return latest
            if cached.run_id != run_id:
                raise _integrity_error()
            if latest.revision < cached.revision:
                raise _integrity_error()
            if latest.revision > cached.revision:
                self._replace_state(run_dir / "state.json", latest)
            return latest
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def transition(
        self,
        run_id: str,
        status: str,
        *,
        failure_code: str | None = None,
        failure_message: str | None = None,
        retryable: bool = False,
        timeline_id: str | None = None,
    ) -> RunState:
        with self._lock:
            current = self.load_state(run_id)
            if status not in VALID_TRANSITIONS[current.status]:
                raise AnalysisError(
                    "invalid_run_transition",
                    f"Run cannot transition from {current.status} to {status}.",
                    retryable=False,
                )
            state = RunState(
                run_id=run_id,
                revision=current.revision + 1,
                status=status,
                updated_at=_utc_now(),
                failure_code=failure_code,
                failure_message=failure_message,
                retryable=retryable,
                timeline_id=timeline_id,
            )
            run_dir = self.runs / run_id
            self._publish_immutable(
                run_dir / "events" / f"{state.revision:08d}.json",
                state.to_record_mapping(),
            )
            self._replace_state(run_dir / "state.json", state)
            return state

    def publish_success(
        self,
        run_id: str,
        timeline: ChordCandidateTimeline,
    ) -> RunState:
        run = self.load_run(run_id)
        state = self.load_state(run_id)
        spec = self.load_spec(run.spec_id)
        if state.status != "running":
            raise AnalysisError(
                "run_not_publishable",
                "Only a running analysis can publish a result.",
                retryable=False,
            )
        if run.spec_id != timeline.spec_id:
            raise AnalysisError(
                "invalid_engine_output",
                "The analysis result does not match the requested input.",
                retryable=False,
            )
        if (
            timeline.timebase != spec.timebase
            or timeline.analyzed_range != spec.analyzed_range
            or timeline.engine != spec.config.engine
        ):
            raise AnalysisError(
                "invalid_engine_output",
                "The analysis result provenance does not match its specification.",
                retryable=False,
            )
        timeline_path = self._digest_path(self.timelines, timeline.id, create=True)
        self._publish_immutable(timeline_path, timeline.to_record_mapping())
        output = {"timeline_id": timeline.id}
        self._publish_immutable(self.runs / run_id / "output.json", output)
        return self.transition(run_id, "succeeded", timeline_id=timeline.id)

    def timeline_for_run(self, run_id: str) -> ChordCandidateTimeline:
        state = self.load_state(run_id)
        if state.status != "succeeded" or state.timeline_id is None:
            raise AnalysisError(
                "timeline_unavailable",
                "A candidate timeline is available only after successful analysis.",
            )
        output = self._read_json(self.runs / run_id / "output.json")
        if set(output) != {"timeline_id"} or output["timeline_id"] != state.timeline_id:
            raise _integrity_error()
        try:
            timeline = timeline_from_mapping(
                self._read_json(self._digest_path(self.timelines, state.timeline_id))
            )
            if timeline.id != state.timeline_id:
                raise _integrity_error()
            return timeline
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def list_runs(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        values = []
        for name in _entry_names(self.runs, self.anchor):
            if not name.startswith("run_"):
                continue
            path = self.runs / name
            _require_dir(path, self.anchor)
            if not _path_exists(
                path / "request.json", self.anchor
            ) or not _path_exists(path / "state.json", self.anchor):
                continue
            run = self.load_run(path.name)
            if source_id is not None and run.source_id != source_id:
                continue
            state = self.load_state(run.id)
            spec = self.load_spec(run.spec_id)
            values.append(_public_run(run, state, spec))
        return sorted(values, key=lambda item: (str(item["created_at"]), str(item["run_id"])))

    def recover_interrupted(self) -> None:
        """Mark nonterminal attempts failed when their owning process is gone."""

        active = self.claims / "active"
        descriptor = _open_claim_file(active, self.anchor)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for name in _entry_names(self.runs, self.anchor):
                if not name.startswith("run_"):
                    continue
                path = self.runs / name
                _require_dir(path, self.anchor)
                if not _path_exists(
                    path / "request.json", self.anchor
                ) or not _path_exists(path / "state.json", self.anchor):
                    continue
                state = self.load_state(path.name)
                if state.status in {"queued", "running", "cancel_requested"}:
                    self.transition(
                        path.name,
                        "failed",
                        failure_code="analysis_interrupted",
                        failure_message=(
                            "The previous Studio session ended during analysis. Retry as a new run."
                        ),
                        retryable=True,
                    )
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def public_status(self, run_id: str) -> dict[str, Any]:
        run = self.load_run(run_id)
        return _public_run(run, self.load_state(run_id), self.load_spec(run.spec_id))

    def claim(self, run_id: str) -> None:
        _validate_run_id(run_id)
        path = self.claims / "active"
        descriptor = _open_claim_file(path, self.anchor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise AnalysisError(
                "analysis_claimed",
                "Another analysis worker owns this project. Wait or retry after it stops.",
            ) from None
        with os.fdopen(os.dup(descriptor), "w", encoding="ascii") as handle:
            handle.truncate(0)
            handle.write(f"{run_id}\n{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._claim_handles[run_id] = descriptor
        _sync_dir(path.parent, self.anchor)

    def release_claim(self, run_id: str) -> None:
        descriptor = self._claim_handles.pop(run_id, None)
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            lines = os.read(descriptor, 256).decode("ascii").splitlines()
            if not lines or lines[0] != run_id:
                raise _integrity_error()
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _digest_path(self, root: Path, identifier: str, *, create: bool = False) -> Path:
        if not identifier.startswith("sha256:") or len(identifier) != 71:
            raise AnalysisError("invalid_analysis_id", "Analysis artifact identifier is invalid.")
        digest = identifier[7:]
        if any(character not in "0123456789abcdef" for character in digest):
            raise AnalysisError("invalid_analysis_id", "Analysis artifact identifier is invalid.")
        parent = root / digest[:2]
        if create:
            _ensure_dir(parent, self.anchor)
        else:
            _require_dir(parent, self.anchor)
        return parent / f"{digest}.json"

    def _publish_immutable(self, path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        try:
            create_text_exclusive(
                path,
                payload,
                stage_prefix=".analysis-",
                mode=0o600,
                sync_directory=True,
                anchor=self.anchor,
            )
        except TargetOccupiedError:
            if self._read_json(path) != value:
                raise _integrity_error() from None
        except OSError:
            raise _integrity_error() from None

    def _replace_state(self, path: Path, state: RunState) -> None:
        payload = json.dumps(state.to_record_mapping(), indent=2, sort_keys=True) + "\n"
        try:
            replace_text(
                path,
                payload,
                stage_prefix=".analysis-state-",
                sync_directory=True,
                anchor=self.anchor,
                mode=0o600,
            )
        except OSError:
            raise _integrity_error() from None

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            with self.anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
        except OSError:
            raise _integrity_error() from None
        try:
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or entry.st_size > _MAX_JSON_BYTES
            ):
                raise _integrity_error()
            payload = bytearray()
            while len(payload) <= _MAX_JSON_BYTES:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, _MAX_JSON_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            if (
                len(payload) > _MAX_JSON_BYTES
                or (entry.st_dev, entry.st_ino, entry.st_size)
                != (after.st_dev, after.st_ino, after.st_size)
            ):
                raise _integrity_error()
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
                parse_float=_reject_float,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            raise _integrity_error() from None
        finally:
            os.close(descriptor)
        if not isinstance(value, dict):
            raise _integrity_error()
        return value


def _public_run(run: AnalysisRun, state: RunState, spec: AnalysisSpec) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "source_id": run.source_id,
        "created_at": run.created_at,
        "retry_of": run.retry_of,
        **state.to_public_mapping(),
        "scope": spec.analyzed_range.to_mapping(),
        "timebase": spec.timebase.to_mapping(),
        "engine": {
            "engine_id": spec.config.engine.engine_id,
            "engine_version": spec.config.engine.engine_version,
            "model_name": spec.config.engine.model_name,
            "model_version": spec.config.engine.model_version,
            "vocabulary_id": spec.config.engine.vocabulary_id,
            "vocabulary_version": spec.config.engine.vocabulary_version,
            "determinism": "deterministic",
        },
    }


def _validate_run_id(value: str) -> None:
    if not value.startswith("run_") or len(value) != 36:
        raise AnalysisError("invalid_run_id", "Analysis run identifier is invalid.")
    try:
        int(value[4:], 16)
    except ValueError:
        raise AnalysisError("invalid_run_id", "Analysis run identifier is invalid.") from None


def _open_claim_file(path: Path, anchor: ProjectAnchor) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            descriptor = os.open(leaf, flags, 0o600, dir_fd=parent_fd)
    except OSError:
        raise _integrity_error() from None
    entry = os.fstat(descriptor)
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o600
        or entry.st_nlink != 1
    ):
        os.close(descriptor)
        raise _integrity_error()
    return descriptor


def _ensure_dir(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path), create=True):
            pass
    except OSError:
        raise _integrity_error() from None


def _require_dir(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)):
            pass
    except OSError:
        raise AnalysisError(
            "analysis_storage_missing", "Analysis storage is not initialized."
        ) from None


def _path_exists(path: Path, anchor: ProjectAnchor) -> bool:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise _integrity_error() from None


def _entry_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            return tuple(sorted(os.listdir(descriptor)))
    except OSError:
        raise _integrity_error() from None


def _json_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    return tuple(name for name in _entry_names(path, anchor) if name.endswith(".json"))


def _sync_dir(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            os.fsync(descriptor)
    except OSError:
        raise _integrity_error() from None


def _integrity_error() -> AnalysisError:
    return AnalysisError(
        "analysis_storage_integrity",
        "Private analysis storage failed an integrity check.",
        retryable=False,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate analysis record key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite analysis JSON value: {value}")


def _reject_float(value: str) -> Any:
    raise ValueError(f"floating-point analysis JSON value: {value}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
