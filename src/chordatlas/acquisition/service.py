from __future__ import annotations

import hashlib
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from chordatlas.acquisition.models import (
    AcquisitionError,
    DirectHttpsSource,
)
from chordatlas.acquisition.store import AcquisitionStore
from chordatlas.acquisition.transport import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    AcquisitionTransport,
    PinnedHttpsTransport,
)
from chordatlas.media import MediaImportError, ProjectMediaStore, RemoteLocator


@dataclass
class _Job:
    run_id: str
    cancel: threading.Event
    thread: threading.Thread | None = None


class AcquisitionService:
    """One-job coordinator for authorized direct HTTPS WAV acquisition."""

    def __init__(
        self,
        project_root: Path,
        *,
        transport: AcquisitionTransport | None = None,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> None:
        self.media = ProjectMediaStore.initialize(project_root)
        self.store = AcquisitionStore.initialize(project_root)
        self.store.recover_interrupted(self._recover_published_output)
        self.transport = transport or PinnedHttpsTransport()
        self.max_download_bytes = max_download_bytes
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._closed = False

    def start(
        self,
        *,
        url: str,
        display_name: str,
        authorization_confirmed: bool,
        idempotency_key: str | None = None,
        retry_of: str | None = None,
    ) -> str:
        # Authorization is checked before URL classification and, critically,
        # before the transport can invoke the resolver.
        if authorization_confirmed is not True:
            raise AcquisitionError(
                "authorization_required",
                "Confirm that you are authorized to process this recording.",
            )
        source = DirectHttpsSource.classify(url)
        safe_name = _safe_display_name(display_name)
        if idempotency_key is not None and (
            not 8 <= len(idempotency_key) <= 128
            or not idempotency_key.isascii()
            or any(ord(character) < 32 for character in idempotency_key)
        ):
            raise AcquisitionError(
                "invalid_idempotency_key",
                "Idempotency key is invalid.",
                retryable=False,
            )
        with self._lock:
            if self._closed:
                raise AcquisitionError("acquisition_closed", "Acquisition service is stopping.")
            active = [job for job in self._jobs.values() if job.thread and job.thread.is_alive()]
            if active:
                raise AcquisitionError(
                    "acquisition_busy",
                    "Another acquisition is active. Wait, cancel it, or retry later.",
                )
            fingerprint = hashlib.sha256(
                f"{source.url}\0{safe_name}\0{retry_of or ''}".encode()
            ).hexdigest()
            if idempotency_key is not None:
                existing = self._existing_idempotency(idempotency_key, fingerprint)
                if existing is not None:
                    return existing
            request = self.store.create_request(
                normalized_url=source.url,
                display_name=safe_name,
                retry_of=retry_of,
            )
            if idempotency_key is not None:
                claimed = self.store.claim_idempotency(
                    idempotency_key,
                    fingerprint,
                    request.id,
                )
                if claimed != request.id:
                    self.store.append_event(
                        request.id,
                        "failed",
                        phase="failed",
                        failure_code="idempotency_superseded",
                        failure_message="An identical acquisition request already exists.",
                        retryable=False,
                    )
                    return claimed
            job = _Job(request.id, threading.Event())
            thread = threading.Thread(
                target=self._supervise,
                args=(job, source),
                name=f"acquisition-{request.id}",
                daemon=False,
            )
            job.thread = thread
            self._jobs[request.id] = job
            try:
                thread.start()
            except Exception:
                self.store.append_event(
                    request.id,
                    "failed",
                    phase="failed",
                    failure_code="supervisor_start_failed",
                    failure_message="The acquisition supervisor could not start. Retry it.",
                )
                raise AcquisitionError(
                    "supervisor_start_failed",
                    "The acquisition supervisor could not start. Retry it.",
                ) from None
            return request.id

    def status(self, run_id: str) -> dict:
        value = self.store.status(run_id)
        assert value is not None
        return value

    def list(self) -> list[dict]:
        return self.store.list()

    def has_active(self) -> bool:
        with self._lock:
            return any(job.thread and job.thread.is_alive() for job in self._jobs.values())

    def cancel(self, run_id: str) -> dict:
        with self._lock:
            state = self.status(run_id)
            if state["status"] in {"succeeded", "failed", "cancelled"}:
                return state
            job = self._jobs.get(run_id)
            if job is None:
                raise AcquisitionError(
                    "acquisition_not_active",
                    "This acquisition is not active in the current session.",
                )
            job.cancel.set()
            if state["status"] == "queued":
                self.store.append_event(run_id, "cancelled", phase="cancelled")
            elif state["status"] == "running":
                self.store.append_event(run_id, "cancel_requested", phase=state["phase"])
            return self.status(run_id)

    def retry(self, run_id: str, *, idempotency_key: str | None = None) -> str:
        state = self.status(run_id)
        if state["status"] not in {"failed", "cancelled"} or (
            state["status"] == "failed" and not state["retryable"]
        ):
            raise AcquisitionError(
                "retry_unavailable",
                "Only retryable failed or cancelled acquisitions can be retried.",
                retryable=False,
            )
        request = self.store.load_request(run_id)
        return self.start(
            url=self.store.private_url(run_id),
            display_name=request.display_name,
            authorization_confirmed=True,
            idempotency_key=idempotency_key,
            retry_of=run_id,
        )

    def forget_locator(self, run_id: str) -> dict:
        with self._lock:
            state = self.status(run_id)
            if state["status"] not in {"succeeded", "failed", "cancelled"}:
                raise AcquisitionError(
                    "acquisition_active",
                    "Wait for the acquisition to finish before removing its private locator.",
                )
            lineage = self.store.lineage(run_id)
            states = [self.status(candidate) for candidate in lineage]
            if any(item["status"] in {"queued", "running", "cancel_requested"} for item in states):
                raise AcquisitionError(
                    "acquisition_active",
                    "Wait for every retry in this acquisition family to finish before removing its locator.",
                )
            for item in states:
                if item["status"] == "succeeded":
                    self.media.forget_remote_locator(str(item["source_id"]))
            for candidate in lineage:
                self.store.forget_private_url(candidate)
            return self.status(run_id)

    def wait(self, run_id: str, timeout: float | None = None) -> dict:
        job = self._jobs.get(run_id)
        if job is not None and job.thread is not None:
            job.thread.join(timeout)
        return self.status(run_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in jobs:
                job.cancel.set()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(20)

    def _existing_idempotency(self, key: str, fingerprint: str) -> str | None:
        # Claim with a sentinel is unsafe, so inspect the private deterministic
        # record through the store's idempotent operation only after creation.
        digest = hashlib.sha256(key.encode()).hexdigest()
        path = self.store.idempotency_root / f"{digest}.json"
        if not path.exists():
            return None
        return self.store.claim_idempotency(key, fingerprint, "acq_" + ("0" * 32))

    def _supervise(self, job: _Job, source: DirectHttpsSource) -> None:
        stage_path = self.media.allocate_private_stage()
        try:
            if job.cancel.is_set():
                if self.status(job.run_id)["status"] == "queued":
                    self.store.append_event(job.run_id, "cancelled", phase="cancelled")
                return
            self.store.append_event(job.run_id, "running", phase="resolving")

            def progress(received: int, total: int) -> None:
                with self._lock:
                    current = self.status(job.run_id)
                    if current["status"] == "running":
                        self.store.append_event(
                            job.run_id,
                            "running",
                            phase="receiving",
                            bytes_received=received,
                            byte_length=total,
                        )

            result = self.transport.download(
                source,
                stage_path,
                progress=progress,
                cancelled=job.cancel.is_set,
                max_bytes=self.max_download_bytes,
            )
            with self._lock:
                state = self.status(job.run_id)
                if job.cancel.is_set() or state["status"] == "cancel_requested":
                    if state["status"] == "running":
                        self.store.append_event(
                            job.run_id, "cancel_requested", phase=state["phase"]
                        )
                    self.store.append_event(job.run_id, "cancelled", phase="cancelled")
                    return
                request = self.store.load_request(job.run_id)
                self.store.append_event(
                    job.run_id,
                    "running",
                    phase="publishing",
                    bytes_received=result.byte_length,
                    byte_length=result.byte_length,
                )
                expected_asset_id = _stage_asset_id(stage_path)
                source_record, asset = self.media.publish_staged_file(
                    stage_path,
                    byte_length=result.byte_length,
                    display_name=request.display_name,
                    authorization_confirmed=True,
                    source_id=request.source_id,
                    locator=RemoteLocator(
                        source_id=request.source_id,
                        channel="direct_https",
                        private_locator=result.final_url,
                    ),
                    expected_asset_id=expected_asset_id,
                    max_upload_bytes=self.max_download_bytes,
                )
                if source_record.id != request.source_id or asset.id != expected_asset_id:
                    raise AcquisitionError(
                        "publication_integrity",
                        "The acquired media failed a publication integrity check.",
                        retryable=False,
                    )
                self.store.publish_output(
                    job.run_id,
                    source_id=source_record.id,
                    asset_id=asset.id,
                )
                self.store.append_event(
                    job.run_id,
                    "succeeded",
                    phase="complete",
                    bytes_received=result.byte_length,
                    byte_length=result.byte_length,
                    retryable=False,
                )
        except (AcquisitionError, MediaImportError) as error:
            if not self._complete_published(job):
                self._fail(job, error)
        except Exception:
            if not self._complete_published(job):
                self._fail(
                    job,
                    AcquisitionError(
                        "acquisition_internal",
                        "Acquisition failed safely. Retry it.",
                    ),
                )
        finally:
            try:
                stage_path.unlink()
            except FileNotFoundError:
                pass

    def _fail(self, job: _Job, error: AcquisitionError | MediaImportError) -> None:
        with self._lock:
            state = self.status(job.run_id)
            if state["status"] in {"succeeded", "failed", "cancelled"}:
                return
            if job.cancel.is_set() or state["status"] == "cancel_requested":
                if state["status"] == "running":
                    self.store.append_event(
                        job.run_id, "cancel_requested", phase=state["phase"]
                    )
                self.store.append_event(job.run_id, "cancelled", phase="cancelled")
            else:
                self.store.append_event(
                    job.run_id,
                    "failed",
                    phase="failed",
                    failure_code=error.code,
                    failure_message=error.public_message,
                    retryable=error.retryable,
                )

    def _recover_published_output(self, run_id: str) -> dict | None:
        request = self.store.load_request(run_id)
        try:
            source = self.media.source(request.source_id)
            asset = self.media.asset_for_source(request.source_id)
        except MediaImportError:
            return None
        if source.asset_id != asset.id:
            return None
        return {"source_id": source.id, "asset_id": asset.id}

    def _complete_published(self, job: _Job) -> bool:
        """Once source visibility wins, never record a contradictory failure."""

        output = self._recover_published_output(job.run_id)
        if output is None:
            return False
        try:
            self.store.publish_output(
                job.run_id,
                source_id=str(output["source_id"]),
                asset_id=str(output["asset_id"]),
            )
            state = self.status(job.run_id)
            if state["status"] in {"running", "cancel_requested"}:
                self.store.append_event(
                    job.run_id,
                    "succeeded",
                    phase="complete",
                    bytes_received=state["bytes_received"],
                    byte_length=state["byte_length"],
                    retryable=False,
                )
        except Exception:
            # Leave the attempt nonterminal. Startup recovery uses the visible
            # source/asset chain to converge it to success; it must not become
            # a terminal failure after publication.
            pass
        return True


def _safe_display_name(value: str) -> str:
    if not isinstance(value, str):
        raise AcquisitionError(
            "invalid_display_name",
            "Enter a safe display name for the acquired recording.",
            retryable=False,
        )
    leaf = value.replace("\\", "/").split("/")[-1]
    leaf = "".join(character for character in leaf if character.isprintable())
    leaf = leaf.strip().strip(".")
    return (leaf or "remote-audio.wav")[:160]


def _stage_asset_id(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise AcquisitionError(
            "unsafe_stage",
            "The private acquisition stage failed an integrity check.",
            retryable=False,
        ) from None
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise AcquisitionError(
                "unsafe_stage",
                "The private acquisition stage failed an integrity check.",
                retryable=False,
            )
        for payload in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(payload)
        after = os.fstat(handle.fileno())
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise AcquisitionError(
                "unsafe_stage",
                "The private acquisition stage changed during validation.",
                retryable=False,
            )
    return f"sha256:{digest.hexdigest()}"
