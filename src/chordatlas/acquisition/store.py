from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from chordatlas.acquisition.models import AcquisitionError, AcquisitionRequest, utc_now

_ACTIVE = {"queued", "running", "cancel_requested"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_MAX_RECORD_BYTES = 1024 * 1024


class AcquisitionStore:
    """Private append-only acquisition requests, secrets, and state events."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.root = self.project_root / ".chordatlas"
        self.acquisitions_root = self.root / "acquisitions"
        self.requests_root = self.acquisitions_root / "requests"
        self.events_root = self.acquisitions_root / "events"
        self.outputs_root = self.acquisitions_root / "outputs"
        self.private_root = self.root / "private" / "acquisitions"
        self.idempotency_root = self.private_root / "idempotency"
        self.tmp_root = self.root / "tmp"

    @classmethod
    def initialize(cls, project_root: Path) -> AcquisitionStore:
        store = cls(project_root)
        for path in (
            store.acquisitions_root,
            store.requests_root,
            store.events_root,
            store.outputs_root,
            store.private_root,
            store.idempotency_root,
        ):
            _ensure_private_directory(path)
        _require_private_directory(store.tmp_root)
        return store

    def create_request(
        self,
        *,
        normalized_url: str,
        display_name: str,
        retry_of: str | None,
    ) -> AcquisitionRequest:
        run_id = f"acq_{secrets.token_hex(16)}"
        source_id = f"src_{secrets.token_hex(16)}"
        timestamp = utc_now()
        request = AcquisitionRequest(
            id=run_id,
            source_id=source_id,
            display_name=display_name,
            url_fingerprint=hashlib.sha256(normalized_url.encode()).hexdigest(),
            authorization_basis="user_attested_authorized",
            authorized_at=timestamp,
            created_at=timestamp,
            retry_of=retry_of,
        )
        _publish_json_exclusive(
            self.tmp_root,
            self.requests_root / f"{run_id}.json",
            request.to_record_mapping(),
        )
        _publish_json_exclusive(
            self.tmp_root,
            self.private_root / f"{run_id}.json",
            {"run_id": run_id, "private_url": normalized_url},
        )
        _ensure_private_directory(self.events_root / run_id)
        self.append_event(run_id, "queued", phase="queued")
        return request

    def claim_idempotency(self, key: str, fingerprint: str, run_id: str) -> str:
        if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 32 for c in key):
            raise AcquisitionError(
                "invalid_idempotency_key",
                "Idempotency key is invalid.",
                retryable=False,
            )
        digest = hashlib.sha256(key.encode()).hexdigest()
        path = self.idempotency_root / f"{digest}.json"
        value = {
            "key_digest": digest,
            "request_fingerprint": fingerprint,
            "run_id": run_id,
        }
        if path.exists():
            existing = _read_json(path)
            if existing["request_fingerprint"] != fingerprint:
                raise AcquisitionError(
                    "idempotency_conflict",
                    "The idempotency key was already used for another acquisition.",
                    retryable=False,
                )
            return str(existing["run_id"])
        try:
            _publish_json_exclusive(self.tmp_root, path, value)
            return run_id
        except AcquisitionError:
            # Another process may win the exclusive link between the existence
            # probe and publication. Read the winner through the same integrity
            # checks and converge only when its request fingerprint matches.
            existing = _read_json(path)
            if existing["request_fingerprint"] != fingerprint:
                raise AcquisitionError(
                    "idempotency_conflict",
                    "The idempotency key was already used for another acquisition.",
                    retryable=False,
                ) from None
            return str(existing["run_id"])

    def load_request(self, run_id: str) -> AcquisitionRequest:
        _validate_run_id(run_id)
        try:
            return AcquisitionRequest.from_mapping(
                _read_json(self.requests_root / f"{run_id}.json")
            )
        except AcquisitionError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def private_url(self, run_id: str) -> str:
        _validate_run_id(run_id)
        path = self.private_root / f"{run_id}.json"
        if not path.exists():
            raise AcquisitionError(
                "locator_forgotten",
                "The private locator was removed. This acquisition cannot be retried.",
                retryable=False,
            )
        try:
            value = _read_json(path)
            if value["run_id"] != run_id:
                raise _integrity_error()
            return str(value["private_url"])
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def forget_private_url(self, run_id: str) -> None:
        _validate_run_id(run_id)
        path = self.private_root / f"{run_id}.json"
        if not path.exists():
            return
        value = _read_json(path)
        if value.get("run_id") != run_id:
            raise _integrity_error()
        path.unlink()
        _sync_directory(path.parent)

    def append_event(
        self,
        run_id: str,
        status: str,
        *,
        phase: str,
        bytes_received: int = 0,
        byte_length: int | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        retryable: bool = True,
    ) -> dict[str, Any]:
        _validate_run_id(run_id)
        current = self.status(run_id, missing_ok=True)
        if current is not None:
            prior = str(current["status"])
            if prior in _TERMINAL:
                raise AcquisitionError(
                    "terminal_acquisition",
                    "The acquisition already has a terminal outcome.",
                    retryable=False,
                )
            allowed = {
                "queued": {"running", "cancelled", "failed"},
                "running": {"running", "cancel_requested", "succeeded", "failed"},
                "cancel_requested": {"cancelled", "failed", "succeeded"},
            }
            if status not in allowed[prior]:
                raise AcquisitionError(
                    "invalid_transition",
                    "The acquisition state transition is invalid.",
                    retryable=False,
                )
            revision = int(current["revision"]) + 1
        else:
            if status != "queued":
                raise AcquisitionError("invalid_transition", "Acquisition must begin queued.")
            revision = 0
        event = {
            "event_schema_version": "1.0.0-draft",
            "run_id": run_id,
            "revision": revision,
            "status": status,
            "phase": phase,
            "bytes_received": bytes_received,
            "byte_length": byte_length,
            "updated_at": utc_now(),
            "failure_code": failure_code,
            "failure_message": failure_message,
            "retryable": retryable,
        }
        directory = self.events_root / run_id
        _require_private_directory(directory)
        _publish_json_exclusive(self.tmp_root, directory / f"{revision:08d}.json", event)
        return event

    def status(self, run_id: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        _validate_run_id(run_id)
        directory = self.events_root / run_id
        if not directory.exists():
            if missing_ok:
                return None
            raise AcquisitionError("unknown_acquisition", "Acquisition was not found.")
        _require_private_directory(directory)
        paths = sorted(directory.glob("*.json"))
        if not paths:
            if missing_ok:
                return None
            raise _integrity_error()
        value = _read_json(paths[-1])
        request = self.load_request(run_id)
        public = {
            "run_id": run_id,
            "source_id": request.source_id,
            "display_name": request.display_name,
            "retry_of": request.retry_of,
            "created_at": request.created_at,
            "revision": int(value["revision"]),
            "status": str(value["status"]),
            "phase": str(value["phase"]),
            "bytes_received": int(value["bytes_received"]),
            "byte_length": (
                None if value["byte_length"] is None else int(value["byte_length"])
            ),
            "updated_at": str(value["updated_at"]),
            "failure_code": (
                None if value["failure_code"] is None else str(value["failure_code"])
            ),
            "failure_message": (
                None
                if value["failure_message"] is None
                else str(value["failure_message"])
            ),
            "retryable": bool(value["retryable"]),
            "locator_retained": (self.private_root / f"{run_id}.json").exists(),
            "cancellable": (
                str(value["status"]) == "queued"
                or (
                    str(value["status"]) == "running"
                    and str(value["phase"]) != "publishing"
                )
            ),
        }
        output_path = self.outputs_root / f"{run_id}.json"
        if str(value["status"]) == "succeeded":
            if not output_path.exists():
                raise _integrity_error()
            output = _read_json(output_path)
            public["output"] = {"source_id": str(output["source_id"])}
        return public

    def list(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.requests_root.glob("acq_*.json")):
            value = self.status(path.stem)
            assert value is not None
            values.append(value)
        return sorted(
            values,
            key=lambda item: (
                item["status"] in _ACTIVE,
                str(item["created_at"]),
                str(item["updated_at"]),
                str(item["run_id"]),
            ),
        )

    def lineage(self, run_id: str) -> tuple[str, ...]:
        """Return the complete connected retry family for one immutable attempt."""

        _validate_run_id(run_id)
        requests = {
            path.stem: self.load_request(path.stem)
            for path in self.requests_root.glob("acq_*.json")
        }
        if run_id not in requests:
            raise AcquisitionError("unknown_acquisition", "Acquisition was not found.")
        related = {run_id}
        changed = True
        while changed:
            changed = False
            for candidate, request in requests.items():
                if candidate in related or request.retry_of in related:
                    before = len(related)
                    related.add(candidate)
                    if request.retry_of is not None:
                        related.add(request.retry_of)
                    changed = changed or len(related) != before
        return tuple(sorted(related))

    def publish_output(self, run_id: str, *, source_id: str, asset_id: str) -> None:
        request = self.load_request(run_id)
        if source_id != request.source_id:
            raise _integrity_error()
        _publish_json_exclusive(
            self.tmp_root,
            self.outputs_root / f"{run_id}.json",
            {
                "output_schema_version": "1.0.0-draft",
                "run_id": run_id,
                "source_id": source_id,
                "asset_id": asset_id,
            },
        )

    def recover_interrupted(self, recover_output=None) -> list[str]:
        recovered: list[str] = []
        for path in sorted(self.requests_root.glob("acq_*.json")):
            run_id = path.stem
            state = self.status(run_id)
            if state is not None and state["status"] in _ACTIVE:
                output = recover_output(run_id) if recover_output is not None else None
                if output is not None:
                    self.publish_output(
                        run_id,
                        source_id=str(output["source_id"]),
                        asset_id=str(output["asset_id"]),
                    )
                    self.append_event(
                        run_id,
                        "succeeded",
                        phase="complete",
                        retryable=False,
                    )
                else:
                    self.append_event(
                        run_id,
                        "failed",
                        phase="failed",
                        failure_code="acquisition_interrupted",
                        failure_message="Acquisition was interrupted. Retry it safely.",
                        retryable=True,
                    )
                recovered.append(run_id)
        for path in self.tmp_root.glob(".acquisition-*.tmp"):
            if path.is_symlink() or not path.is_file():
                raise _integrity_error()
            path.unlink()
        return recovered


def _validate_run_id(value: str) -> None:
    if not re_full_acquisition(value):
        raise AcquisitionError("invalid_acquisition_id", "Acquisition identifier is invalid.")


def re_full_acquisition(value: str) -> bool:
    return len(value) == 36 and value.startswith("acq_") and all(
        character in "0123456789abcdef" for character in value[4:]
    )


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _require_private_directory(path)


def _require_private_directory(path: Path) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        raise _integrity_error() from None
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o700
    ):
        raise _integrity_error()


def _publish_json_exclusive(stage_root: Path, path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=".acquisition-", suffix=".tmp", dir=stage_root)
    stage = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(stage, path, follow_symlinks=False)
            _sync_directory(path.parent)
        except FileExistsError:
            if _read_json(path) != value:
                raise _integrity_error() from None
    finally:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    value = None
    for attempt in range(20):
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise _integrity_error() from None
        try:
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_size > _MAX_RECORD_BYTES
            ):
                raise _integrity_error()
            if entry.st_nlink != 1:
                if attempt < 19:
                    os.close(descriptor)
                    descriptor = -1
                    time.sleep(0.001)
                    continue
                raise _integrity_error()
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                value = json.load(handle)
            break
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if not isinstance(value, dict):
        raise _integrity_error()
    return value


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _integrity_error() -> AcquisitionError:
    return AcquisitionError(
        "acquisition_storage_integrity",
        "Private acquisition records failed an integrity check.",
        retryable=False,
    )
