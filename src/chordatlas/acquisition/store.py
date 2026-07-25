from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from chordatlas._fs import ProjectAnchor, TargetOccupiedError, create_text_exclusive
from chordatlas.acquisition.models import (
    AcquisitionError,
    AcquisitionRequest,
    DirectHttpsSource,
    utc_now,
)

_ACTIVE = {"queued", "running", "cancel_requested"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_MAX_RECORD_BYTES = 1024 * 1024


class AcquisitionStore:
    """Private append-only acquisition requests, secrets, and state events."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.root = self.project_root / ".chordatlas"
        try:
            self.anchor = ProjectAnchor.for_project(self.project_root)
        except OSError:
            raise _integrity_error() from None
        self.acquisitions_root = self.root / "acquisitions"
        self.requests_root = self.acquisitions_root / "requests"
        self.events_root = self.acquisitions_root / "events"
        self.outputs_root = self.acquisitions_root / "outputs"
        self.private_root = self.root / "private" / "acquisitions"
        self.idempotency_root = self.private_root / "idempotency"
        self.claims_root = self.private_root / "claims"
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
            store.claims_root,
        ):
            _ensure_private_directory(path, store.anchor)
        _require_private_directory_anchored(store.tmp_root, store.anchor)
        return store

    def _verify_anchor(self) -> None:
        try:
            self.anchor.verify()
        except OSError:
            raise _integrity_error() from None

    def create_request(
        self,
        *,
        normalized_url: str,
        display_name: str,
        retry_of: str | None,
    ) -> AcquisitionRequest:
        self._verify_anchor()
        request = self._new_request(
            normalized_url=normalized_url,
            display_name=display_name,
            retry_of=retry_of,
        )
        self._publish_request(request, normalized_url=normalized_url)
        return request

    def create_claimed_request(
        self,
        *,
        normalized_url: str,
        display_name: str,
        retry_of: str | None,
    ) -> tuple[AcquisitionRequest, int]:
        """Acquire the recovery exclusion before publishing an active request."""

        self._verify_anchor()
        request = self._new_request(
            normalized_url=normalized_url,
            display_name=display_name,
            retry_of=retry_of,
        )
        claim = self._create_lifecycle_claim(request.id)
        try:
            self._publish_request(request, normalized_url=normalized_url)
        except BaseException:
            self.release_lifecycle_claim(request.id, claim, cleanup=True)
            raise
        return request, claim

    def _new_request(
        self,
        *,
        normalized_url: str,
        display_name: str,
        retry_of: str | None,
    ) -> AcquisitionRequest:
        run_id = f"acq_{secrets.token_hex(16)}"
        source_id = f"src_{secrets.token_hex(16)}"
        timestamp = utc_now()
        return AcquisitionRequest(
            id=run_id,
            source_id=source_id,
            display_name=display_name,
            url_fingerprint=hashlib.sha256(normalized_url.encode()).hexdigest(),
            authorization_basis="user_attested_authorized",
            authorized_at=timestamp,
            created_at=timestamp,
            retry_of=retry_of,
        )

    def _publish_request(
        self,
        request: AcquisitionRequest,
        *,
        normalized_url: str,
    ) -> None:
        _publish_json_exclusive(
            self.anchor,
            self.requests_root / f"{request.id}.json",
            request.to_record_mapping(),
        )
        _publish_json_exclusive(
            self.anchor,
            self.private_root / f"{request.id}.json",
            {"run_id": request.id, "private_url": normalized_url},
        )
        _ensure_private_directory(self.events_root / request.id, self.anchor)
        self.append_event(request.id, "queued", phase="queued")

    def _create_lifecycle_claim(self, run_id: str) -> int:
        _validate_run_id(run_id)
        descriptor = -1
        try:
            with self.anchor.directory(
                self.anchor.relative(self.claims_root)
            ) as parent_fd:
                descriptor = os.open(
                    f"{run_id}.lock",
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o600,
                    dir_fd=parent_fd,
                )
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fsync(descriptor)
                os.fsync(parent_fd)
            return descriptor
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise _integrity_error() from None

    def release_lifecycle_claim(
        self,
        run_id: str,
        descriptor: int,
        *,
        cleanup: bool,
    ) -> None:
        """Release one held claim; remove its name only after terminal state."""

        _validate_run_id(run_id)
        try:
            entry = os.fstat(descriptor)
            path = self.claims_root / f"{run_id}.lock"
            with self.anchor.parent(path) as (parent_fd, leaf):
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise _integrity_error()
        finally:
            os.close(descriptor)
        if cleanup:
            _unlink_private(path, self.anchor)

    def claim_idempotency(self, key: str, fingerprint: str, run_id: str) -> str:
        self._verify_anchor()
        if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 32 for c in key):
            raise AcquisitionError(
                "invalid_idempotency_key",
                "Idempotency key is invalid.",
                retryable=False,
            )
        _validate_fingerprint(fingerprint)
        _validate_run_id(run_id)
        digest = hashlib.sha256(key.encode()).hexdigest()
        path = self.idempotency_root / f"{digest}.json"
        value = {
            "key_digest": digest,
            "request_fingerprint": fingerprint,
            "run_id": run_id,
        }
        if _path_exists(path, self.anchor):
            existing = _read_json(path, self.anchor)
            return self._validated_idempotency(existing, digest, fingerprint)
        request = self.load_request(run_id)
        if fingerprint != idempotency_fingerprint(
            url_fingerprint=request.url_fingerprint,
            display_name=request.display_name,
            retry_of=request.retry_of,
        ):
            raise _integrity_error()
        try:
            _publish_json_exclusive(self.anchor, path, value)
            return run_id
        except AcquisitionError:
            # Another process may win the exclusive link between the existence
            # probe and publication. Read the winner through the same integrity
            # checks and converge only when its request fingerprint matches.
            existing = _read_json(path, self.anchor)
            return self._validated_idempotency(existing, digest, fingerprint)

    def lookup_idempotency(self, key: str, fingerprint: str) -> str | None:
        self._verify_anchor()
        if not 8 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 32 for c in key):
            raise AcquisitionError(
                "invalid_idempotency_key",
                "Idempotency key is invalid.",
                retryable=False,
            )
        _validate_fingerprint(fingerprint)
        digest = hashlib.sha256(key.encode()).hexdigest()
        path = self.idempotency_root / f"{digest}.json"
        if not _path_exists(path, self.anchor):
            return None
        return self._validated_idempotency(_read_json(path, self.anchor), digest, fingerprint)

    def _validated_idempotency(
        self,
        value: dict[str, Any],
        digest: str,
        fingerprint: str,
    ) -> str:
        if (
            set(value) != {"key_digest", "request_fingerprint", "run_id"}
            or value.get("key_digest") != digest
            or not _valid_fingerprint(value.get("request_fingerprint"))
            or not isinstance(value.get("run_id"), str)
        ):
            raise _integrity_error()
        existing_run = str(value["run_id"])
        _validate_run_id(existing_run)
        request = self.load_request(existing_run)
        if value["request_fingerprint"] != idempotency_fingerprint(
            url_fingerprint=request.url_fingerprint,
            display_name=request.display_name,
            retry_of=request.retry_of,
        ):
            raise _integrity_error()
        if value["request_fingerprint"] != fingerprint:
            raise AcquisitionError(
                "idempotency_conflict",
                "The idempotency key was already used for another acquisition.",
                retryable=False,
            )
        return existing_run

    def load_request(self, run_id: str) -> AcquisitionRequest:
        self._verify_anchor()
        _validate_run_id(run_id)
        try:
            return AcquisitionRequest.from_mapping(
                _read_json(self.requests_root / f"{run_id}.json", self.anchor)
            )
        except AcquisitionError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def private_url(self, run_id: str) -> str:
        self._verify_anchor()
        _validate_run_id(run_id)
        path = self.private_root / f"{run_id}.json"
        if not _path_exists(path, self.anchor):
            raise AcquisitionError(
                "locator_forgotten",
                "The private locator was removed. This acquisition cannot be retried.",
                retryable=False,
            )
        try:
            value = _read_json(path, self.anchor)
            if (
                set(value) != {"run_id", "private_url"}
                or value.get("run_id") != run_id
                or not isinstance(value.get("private_url"), str)
            ):
                raise _integrity_error()
            source = DirectHttpsSource.classify(value["private_url"])
            request = self.load_request(run_id)
            if (
                source.url != value["private_url"]
                or hashlib.sha256(source.url.encode()).hexdigest()
                != request.url_fingerprint
            ):
                raise _integrity_error()
            return source.url
        except AcquisitionError as error:
            if error.code == "acquisition_storage_integrity":
                raise
            raise _integrity_error() from None
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def forget_private_url(self, run_id: str) -> None:
        self._verify_anchor()
        _validate_run_id(run_id)
        path = self.private_root / f"{run_id}.json"
        if not _path_exists(path, self.anchor):
            return
        self.private_url(run_id)
        _unlink_private(path, self.anchor)

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
        self._verify_anchor()
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
            if status in _TERMINAL | {"cancel_requested"}:
                if bytes_received == 0 and int(current["bytes_received"]) > 0:
                    bytes_received = int(current["bytes_received"])
                if byte_length is None and current["byte_length"] is not None:
                    byte_length = int(current["byte_length"])
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
        _require_private_directory_anchored(directory, self.anchor)
        _publish_json_exclusive(self.anchor, directory / f"{revision:08d}.json", event)
        return event

    def status(self, run_id: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        self._verify_anchor()
        _validate_run_id(run_id)
        directory = self.events_root / run_id
        if not _path_exists(directory, self.anchor):
            if missing_ok:
                return None
            raise AcquisitionError("unknown_acquisition", "Acquisition was not found.")
        _require_private_directory_anchored(directory, self.anchor)
        names = _json_names(directory, self.anchor)
        if not names:
            if missing_ok:
                return None
            raise _integrity_error()
        events = []
        for revision, name in enumerate(names):
            if name != f"{revision:08d}.json":
                raise _integrity_error()
            value = _read_json(directory / name, self.anchor)
            _validate_event(value, run_id=run_id, revision=revision)
            events.append(value)
        _validate_event_sequence(events)
        value = events[-1]
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
            "locator_retained": _path_exists(
                self.private_root / f"{run_id}.json",
                self.anchor,
            ),
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
            if not _path_exists(output_path, self.anchor):
                raise _integrity_error()
            output = _read_json(output_path, self.anchor)
            public["output"] = {"source_id": str(output["source_id"])}
        return public

    def list(self) -> list[dict[str, Any]]:
        self._verify_anchor()
        values = []
        for name in _json_names(self.requests_root, self.anchor):
            if not name.startswith("acq_"):
                continue
            value = self.status(Path(name).stem)
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

        self._verify_anchor()
        _validate_run_id(run_id)
        requests = {
            Path(name).stem: self.load_request(Path(name).stem)
            for name in _json_names(self.requests_root, self.anchor)
            if name.startswith("acq_")
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
        self._verify_anchor()
        request = self.load_request(run_id)
        if source_id != request.source_id:
            raise _integrity_error()
        _publish_json_exclusive(
            self.anchor,
            self.outputs_root / f"{run_id}.json",
            {
                "output_schema_version": "1.0.0-draft",
                "run_id": run_id,
                "source_id": source_id,
                "asset_id": asset_id,
            },
        )

    def recover_interrupted(self, recover_output=None) -> list[str]:
        self._verify_anchor()
        recovered: list[str] = []
        orphan_stages, live_runs = _orphan_upload_stages(self.tmp_root, self.anchor)
        orphan_claims, claimed_runs = _lifecycle_claims(
            self.claims_root,
            self.anchor,
        )
        live_runs.update(claimed_runs)
        for name in _json_names(self.requests_root, self.anchor):
            if not name.startswith("acq_"):
                continue
            run_id = Path(name).stem
            state = self.status(run_id)
            if (
                state is not None
                and state["status"] in _ACTIVE
                and run_id not in live_runs
            ):
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
        for run_id, path in orphan_claims:
            try:
                state = self.status(run_id)
            except AcquisitionError as error:
                if error.code == "unknown_acquisition":
                    continue
                raise
            if state is not None and state["status"] in _TERMINAL:
                _unlink_private(path, self.anchor)
        for run_id, path in orphan_stages:
            try:
                state = self.status(run_id)
            except AcquisitionError as error:
                if error.code == "unknown_acquisition":
                    continue
                raise
            if state is None or state["status"] in _ACTIVE:
                continue
            try:
                _unlink_private(path, self.anchor)
            except AcquisitionError:
                raise _integrity_error()
        for name in _entry_names(self.tmp_root, self.anchor):
            if not name.startswith(".acquisition-") or not name.endswith(".tmp"):
                continue
            path = self.tmp_root / name
            try:
                _unlink_private(path, self.anchor)
            except AcquisitionError:
                raise _integrity_error()
        return recovered


def idempotency_fingerprint(
    *,
    url_fingerprint: str,
    display_name: str,
    retry_of: str | None,
) -> str:
    """Bind an idempotent operation to every immutable request input."""

    return hashlib.sha256(
        f"{url_fingerprint}\0{display_name}\0{retry_of or ''}".encode()
    ).hexdigest()


def _validate_run_id(value: str) -> None:
    if not re_full_acquisition(value):
        raise AcquisitionError("invalid_acquisition_id", "Acquisition identifier is invalid.")


def _validate_fingerprint(value: str) -> None:
    if not _valid_fingerprint(value):
        raise AcquisitionError(
            "acquisition_storage_integrity",
            "Private acquisition storage failed an integrity check.",
            retryable=False,
        )


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def re_full_acquisition(value: str) -> bool:
    return len(value) == 36 and value.startswith("acq_") and all(
        character in "0123456789abcdef" for character in value[4:]
    )


def _ensure_private_directory(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path), create=True):
            pass
    except OSError:
        raise _integrity_error() from None


def _require_private_directory_anchored(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)):
            pass
    except OSError:
        raise _integrity_error() from None


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


def _publish_json_exclusive(
    anchor: ProjectAnchor,
    path: Path,
    value: dict[str, Any],
) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        create_text_exclusive(
            path,
            payload,
            stage_prefix=".acquisition-",
            mode=0o600,
            sync_directory=True,
            anchor=anchor,
        )
    except TargetOccupiedError:
        if _read_json(path, anchor) != value:
            raise _integrity_error() from None
    except OSError:
        raise _integrity_error() from None


def _read_json(path: Path, anchor: ProjectAnchor) -> dict[str, Any]:
    value = None
    for attempt in range(20):
        try:
            with anchor.parent(path) as (parent_fd, leaf):
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
            payload = _read_stable_descriptor(descriptor, entry)
            value = json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
            break
        except (OSError, UnicodeError, ValueError):
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if not isinstance(value, dict):
        raise _integrity_error()
    return value


def _read_stable_descriptor(descriptor: int, before: os.stat_result) -> bytes:
    payload = bytearray()
    while len(payload) <= _MAX_RECORD_BYTES:
        chunk = os.read(
            descriptor,
            min(64 * 1024, _MAX_RECORD_BYTES + 1 - len(payload)),
        )
        if not chunk:
            break
        payload.extend(chunk)
    after = os.fstat(descriptor)
    if (
        len(payload) > _MAX_RECORD_BYTES
        or len(payload) != before.st_size
        or (after.st_dev, after.st_ino, after.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
    ):
        raise _integrity_error()
    return bytes(payload)


def _orphan_upload_stages(
    tmp_root: Path,
    anchor: ProjectAnchor,
) -> tuple[list[tuple[str, Path]], set[str]]:
    orphans: list[tuple[str, Path]] = []
    live_runs: set[str] = set()
    for name in _entry_names(tmp_root, anchor):
        run_id = _upload_stage_owner(name)
        if run_id is None:
            continue
        path = tmp_root / name
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise _integrity_error()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                live_runs.add(run_id)
            else:
                orphans.append((run_id, path))
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except AcquisitionError:
            raise
        except OSError:
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return orphans, live_runs


def _lifecycle_claims(
    claims_root: Path,
    anchor: ProjectAnchor,
) -> tuple[list[tuple[str, Path]], set[str]]:
    orphans: list[tuple[str, Path]] = []
    live_runs: set[str] = set()
    for name in _entry_names(claims_root, anchor):
        if not name.endswith(".lock"):
            raise _integrity_error()
        run_id = name.removesuffix(".lock")
        if not re_full_acquisition(run_id):
            raise _integrity_error()
        path = claims_root / name
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or entry.st_size != 0
                or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise _integrity_error()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                live_runs.add(run_id)
            else:
                orphans.append((run_id, path))
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except AcquisitionError:
            raise
        except OSError:
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return orphans, live_runs


def _upload_stage_owner(name: str) -> str | None:
    prefix = ".upload-"
    suffix = ".tmp"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    body = name[len(prefix) : -len(suffix)]
    try:
        run_id, token = body.rsplit("-", 1)
    except ValueError:
        return None
    if not re_full_acquisition(run_id) or len(token) != 16 or any(
        character not in "0123456789abcdef" for character in token
    ):
        return None
    return run_id


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
            names = tuple(sorted(os.listdir(descriptor)))
    except OSError:
        raise _integrity_error() from None
    if any(Path(name).name != name for name in names):
        raise _integrity_error()
    return names


def _json_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    names = tuple(
        name
        for name in _entry_names(path, anchor)
        if not (name.startswith(".acquisition-") and name.endswith(".tmp"))
    )
    if any(not name.endswith(".json") for name in names):
        raise _integrity_error()
    return names


def _unlink_private(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            descriptor = os.open(
                leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                entry = os.fstat(descriptor)
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or entry.st_nlink != 1
                    or stat.S_IMODE(entry.st_mode) != 0o600
                    or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
                ):
                    raise _integrity_error()
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        return
    except AcquisitionError:
        raise
    except OSError:
        raise _integrity_error() from None


def _validate_event(value: dict[str, Any], *, run_id: str, revision: int) -> None:
    expected = {
        "event_schema_version",
        "run_id",
        "revision",
        "status",
        "phase",
        "bytes_received",
        "byte_length",
        "updated_at",
        "failure_code",
        "failure_message",
        "retryable",
    }
    status = value.get("status")
    phase = value.get("phase")
    byte_length = value.get("byte_length")
    failure_code = value.get("failure_code")
    failure_message = value.get("failure_message")
    allowed_phases = {
        "queued": {"queued"},
        "running": {"resolving", "receiving", "publishing"},
        "cancel_requested": {"resolving", "receiving", "publishing"},
        "succeeded": {"complete"},
        "failed": {"failed"},
        "cancelled": {"cancelled"},
    }
    if (
        set(value) != expected
        or value.get("event_schema_version") != "1.0.0-draft"
        or value.get("run_id") != run_id
        or type(value.get("revision")) is not int
        or value["revision"] != revision
        or status not in allowed_phases
        or phase not in allowed_phases[status]
        or type(value.get("bytes_received")) is not int
        or value["bytes_received"] < 0
        or (
            byte_length is not None
            and (type(byte_length) is not int or byte_length < value["bytes_received"])
        )
        or not _is_timestamp(value.get("updated_at"))
        or type(value.get("retryable")) is not bool
    ):
        raise _integrity_error()
    if status == "failed":
        if (
            not isinstance(failure_code, str)
            or not failure_code
            or not isinstance(failure_message, str)
            or not failure_message
        ):
            raise _integrity_error()
    elif failure_code is not None or failure_message is not None:
        raise _integrity_error()


def _validate_event_sequence(events: list[dict[str, Any]]) -> None:
    if not events or events[0]["status"] != "queued":
        raise _integrity_error()
    allowed = {
        "queued": {"running", "cancelled", "failed"},
        "running": {"running", "cancel_requested", "succeeded", "failed"},
        "cancel_requested": {"cancelled", "failed", "succeeded"},
    }
    for prior, current in zip(events, events[1:]):
        prior_status = str(prior["status"])
        current_status = str(current["status"])
        if current_status not in allowed.get(prior_status, set()):
            raise _integrity_error()


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def _integrity_error() -> AcquisitionError:
    return AcquisitionError(
        "acquisition_storage_integrity",
        "Private acquisition records failed an integrity check.",
        retryable=False,
    )
