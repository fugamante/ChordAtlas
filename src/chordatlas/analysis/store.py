from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chordatlas.analysis.models import (
    AnalysisError,
    AnalysisRun,
    AnalysisSpec,
    ChordCandidateTimeline,
    RunState,
    VALID_TRANSITIONS,
    canonical_json,
    config_from_mapping,
    spec_from_mapping,
    timeline_from_mapping,
)

_MAX_JSON_BYTES = 8 * 1024 * 1024


class AnalysisStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.root = self.project_root / ".chordatlas" / "analysis"
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
        media_root = store.project_root / ".chordatlas"
        _require_dir(media_root)
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
            _ensure_dir(path)
        return store

    def publish_spec(self, spec: AnalysisSpec) -> None:
        path = self._digest_path(self.specs, spec.id, create=True)
        self._publish_immutable(path, {"id": spec.id, **spec.identity_mapping()})

    def load_spec(self, spec_id: str) -> AnalysisSpec:
        try:
            return spec_from_mapping(self._read_json(self._digest_path(self.specs, spec_id)))
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
        _ensure_dir(run_dir)
        _ensure_dir(run_dir / "events")
        self._publish_immutable(run_dir / "request.json", run.to_record_mapping())
        state = RunState(run.id, 0, "queued", _utc_now())
        self._publish_immutable(run_dir / "events" / "00000000.json", state.to_record_mapping())
        self._replace_state(run_dir / "state.json", state)

    def load_run(self, run_id: str) -> AnalysisRun:
        _validate_run_id(run_id)
        try:
            value = self._read_json(self.runs / run_id / "request.json")
            return AnalysisRun(
                id=str(value["id"]),
                spec_id=str(value["spec_id"]),
                source_id=str(value["source_id"]),
                created_at=str(value["created_at"]),
                retry_of=None if value["retry_of"] is None else str(value["retry_of"]),
            )
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_state(self, run_id: str) -> RunState:
        _validate_run_id(run_id)
        run_dir = self.runs / run_id
        try:
            events = sorted((run_dir / "events").glob("*.json"))
            if not events:
                raise _integrity_error()
            latest = _state_from_mapping(self._read_json(events[-1]))
            try:
                cached = _state_from_mapping(self._read_json(run_dir / "state.json"))
            except AnalysisError:
                self._replace_state(run_dir / "state.json", latest)
                return latest
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
        if output.get("timeline_id") != state.timeline_id:
            raise _integrity_error()
        try:
            return timeline_from_mapping(
                self._read_json(self._digest_path(self.timelines, state.timeline_id))
            )
        except AnalysisError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def list_runs(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.runs.glob("run_*")):
            if path.is_symlink() or not path.is_dir():
                raise _integrity_error()
            if not (path / "request.json").exists() or not (path / "state.json").exists():
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
        descriptor = _open_claim_file(active)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for path in sorted(self.runs.glob("run_*")):
                if not (path / "request.json").exists() or not (path / "state.json").exists():
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
        descriptor = _open_claim_file(path)
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
        _sync_dir(path.parent)

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
            _ensure_dir(parent)
        else:
            _require_dir(parent)
        return parent / f"{digest}.json"

    def _publish_immutable(self, path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        stage = _stage_text(self.tmp, payload)
        try:
            try:
                os.link(stage, path, follow_symlinks=False)
                _sync_dir(path.parent)
            except FileExistsError:
                if self._read_json(path) != value:
                    raise _integrity_error() from None
        finally:
            stage.unlink(missing_ok=True)

    def _replace_state(self, path: Path, state: RunState) -> None:
        payload = json.dumps(state.to_record_mapping(), indent=2, sort_keys=True) + "\n"
        stage = _stage_text(self.tmp, payload)
        try:
            os.replace(stage, path)
            os.chmod(path, 0o600)
            _sync_dir(path.parent)
        finally:
            stage.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise _integrity_error()
        _require_file(path)
        if path.stat().st_size > _MAX_JSON_BYTES:
            raise _integrity_error()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _integrity_error() from None
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


def _state_from_mapping(value: dict[str, Any]) -> RunState:
    return RunState(
        run_id=str(value["run_id"]),
        revision=int(value["revision"]),
        status=str(value["status"]),
        updated_at=str(value["updated_at"]),
        failure_code=None if value["failure_code"] is None else str(value["failure_code"]),
        failure_message=(
            None if value["failure_message"] is None else str(value["failure_message"])
        ),
        retryable=bool(value["retryable"]),
        timeline_id=None if value["timeline_id"] is None else str(value["timeline_id"]),
    )


def _validate_run_id(value: str) -> None:
    if not value.startswith("run_") or len(value) != 36:
        raise AnalysisError("invalid_run_id", "Analysis run identifier is invalid.")
    try:
        int(value[4:], 16)
    except ValueError:
        raise AnalysisError("invalid_run_id", "Analysis run identifier is invalid.") from None


def _stage_text(root: Path, payload: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".analysis-", suffix=".tmp", dir=root)
    path = Path(name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _open_claim_file(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
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


def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _require_dir(path)


def _require_dir(path: Path) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        raise AnalysisError("analysis_storage_missing", "Analysis storage is not initialized.") from None
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o700
    ):
        raise _integrity_error()


def _require_file(path: Path) -> None:
    entry = path.stat()
    if entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o600:
        raise _integrity_error()


def _sync_dir(path: Path) -> None:
    descriptor = os.open(path, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _integrity_error() -> AnalysisError:
    return AnalysisError(
        "analysis_storage_integrity",
        "Private analysis storage failed an integrity check.",
        retryable=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
