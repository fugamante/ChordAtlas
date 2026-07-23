from __future__ import annotations

import json
import multiprocessing
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from chordatlas.analysis.baseline import analyze_pcm16_wav
from chordatlas.analysis.models import (
    AnalysisConfig,
    AnalysisError,
    AnalysisSpec,
    FrameRange,
    Timebase,
    spec_from_mapping,
    timeline_from_mapping,
)
from chordatlas.analysis.store import AnalysisStore
from chordatlas.media.store import ProjectMediaStore

_MAX_WORKER_MESSAGE_BYTES = 8 * 1024 * 1024
_WORKER_ERRORS = {
    "analysis_cancelled": ("Analysis was cancelled.", True),
    "analysis_range_too_long": (
        "The baseline engine accepts at most ten minutes per run. Analyze a shorter loop.",
        True,
    ),
    "analysis_range_too_short": ("Choose at least a quarter-second analysis range.", True),
    "decode_failed": ("The authorized WAV could not be analyzed.", True),
    "invalid_parameters": ("The analysis configuration is invalid.", False),
    "unsupported_media": ("The baseline engine requires PCM16 WAV.", False),
    "worker_failed": ("The analysis worker failed safely. Retry the run.", True),
}


@dataclass
class _Job:
    run_id: str
    cancel: threading.Event
    thread: threading.Thread | None = None
    process: multiprocessing.Process | None = None
    worker_cancel: Any = None


class AnalysisService:
    """One-worker local coordinator. Public methods never expose trusted paths."""

    def __init__(
        self,
        project_root: Path,
        *,
        wall_timeout_seconds: float = 120,
        cancel_grace_seconds: float = 2,
    ) -> None:
        self.store = AnalysisStore.initialize(project_root)
        self.media = ProjectMediaStore.open(project_root)
        self.store.recover_interrupted()
        self.wall_timeout_seconds = wall_timeout_seconds
        self.cancel_grace_seconds = cancel_grace_seconds
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._closed = False

    def start(
        self,
        *,
        source_id: str,
        frame_range: FrameRange | None = None,
        params: Mapping[str, int] | None = None,
        seed: int = 0,
        retry_of: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        with self._lock:
            if self._closed:
                raise AnalysisError("analysis_closed", "Analysis service is stopping.")
            try:
                source = self.media.source(source_id)
                asset = self.media.asset_for_source(source_id)
                resolved = self.media.audio_path_for_source(source_id)
            except Exception as error:
                if isinstance(error, AnalysisError):
                    raise
                raise AnalysisError(
                    "media_unavailable",
                    "The authorized media asset is unavailable.",
                    retryable=False,
                ) from None
            config = AnalysisConfig.baseline()
            if params is not None:
                merged = dict(config.parameters)
                for name, value in params.items():
                    if (
                        name not in merged
                        or not isinstance(value, int)
                        or isinstance(value, bool)
                    ):
                        raise AnalysisError("invalid_parameters", "Analysis parameters are invalid.")
                    merged[name] = value
                config = AnalysisConfig(config.engine, tuple(sorted(merged.items())), seed)
            elif seed != 0:
                config = AnalysisConfig(config.engine, config.parameters, seed)
            selected = frame_range or FrameRange(0, asset.timebase.duration_frames)
            spec = AnalysisSpec.create(
                asset_id=asset.id,
                timebase=asset.timebase,
                analyzed_range=selected,
                config=config,
            )
            request_fingerprint = f"{source.id}:{spec.id}:{retry_of or ''}"
            if idempotency_key is not None:
                if not 8 <= len(idempotency_key) <= 128 or not idempotency_key.isascii():
                    raise AnalysisError("invalid_idempotency_key", "Idempotency key is invalid.")
                existing = self._idempotency.get(idempotency_key)
                if existing is not None:
                    if existing[0] != request_fingerprint:
                        raise AnalysisError(
                            "idempotency_conflict",
                            "The idempotency key was already used for different analysis inputs.",
                            retryable=False,
                        )
                    return existing[1]
            if any(job.thread is not None and job.thread.is_alive() for job in self._jobs.values()):
                raise AnalysisError(
                    "analysis_busy",
                    "Another analysis is active. Wait, cancel it, or retry later.",
                )
            run = self.store.create_claimed_run(
                spec,
                source_id=source_id,
                retry_of=retry_of,
            )
            job = _Job(run.id, threading.Event())
            thread = threading.Thread(
                target=self._supervise,
                args=(job, resolved, spec),
                daemon=False,
                name=f"analysis-{run.id}",
            )
            job.thread = thread
            self._jobs[run.id] = job
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = (request_fingerprint, run.id)
            try:
                thread.start()
            except Exception:
                if thread.is_alive():
                    return run.id
                self._jobs.pop(run.id, None)
                if idempotency_key is not None:
                    self._idempotency.pop(idempotency_key, None)
                self.store.transition(
                    run.id,
                    "failed",
                    failure_code="supervisor_start_failed",
                    failure_message="The local analysis supervisor could not start. Retry the run.",
                    retryable=True,
                )
                self.store.release_claim(run.id)
                raise AnalysisError(
                    "supervisor_start_failed",
                    "The local analysis supervisor could not start. Retry the run.",
                    retryable=True,
                ) from None
            return run.id

    def retry(self, run_id: str, *, idempotency_key: str | None = None) -> str:
        run = self.store.load_run(run_id)
        state = self.store.load_state(run_id)
        if state.status not in {"failed", "cancelled"} or (
            state.status == "failed" and not state.retryable
        ):
            raise AnalysisError(
                "retry_unavailable",
                "Only retryable failed or cancelled runs can be retried.",
                retryable=False,
            )
        spec = self.store.load_spec(run.spec_id)
        return self.start(
            source_id=run.source_id,
            frame_range=spec.analyzed_range,
            params=dict(spec.config.parameters),
            seed=spec.config.random_seed,
            retry_of=run.id,
            idempotency_key=idempotency_key,
        )

    def list(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_runs(source_id=source_id)

    def status(self, run_id: str) -> dict[str, Any]:
        return self.store.public_status(run_id)

    def timeline(self, run_id: str) -> dict[str, Any]:
        return self.store.timeline_for_run(run_id).to_public_mapping()

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.store.load_state(run_id)
            if state.status in {"cancelled", "failed", "succeeded"}:
                return self.store.public_status(run_id)
            job = self._jobs.get(run_id)
            if job is None:
                raise AnalysisError(
                    "analysis_not_active",
                    "This run is not active in the current Studio session.",
                )
            if state.status == "queued":
                job.cancel.set()
            elif state.status == "running":
                self.store.transition(run_id, "cancel_requested")
                job.cancel.set()
                if job.worker_cancel is not None:
                    job.worker_cancel.set()
            return self.store.public_status(run_id)

    def has_active(self) -> bool:
        with self._lock:
            return any(job.thread is not None and job.thread.is_alive() for job in self._jobs.values())

    def close(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in jobs:
                job.cancel.set()
                if job.worker_cancel is not None:
                    job.worker_cancel.set()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(self.cancel_grace_seconds + 5)

    def _supervise(self, job: _Job, audio_path: Path, spec: AnalysisSpec) -> None:
        try:
            if job.cancel.is_set():
                self.store.transition(job.run_id, "cancelled")
                return
            self.store.transition(job.run_id, "running")
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            worker_cancel = context.Event()
            process = context.Process(
                target=_worker_main,
                args=(sender, worker_cancel, str(audio_path), {"id": spec.id, **spec.identity_mapping()}),
                daemon=False,
            )
            job.process = process
            job.worker_cancel = worker_cancel
            process.start()
            sender.close()
            deadline = time.monotonic() + self.wall_timeout_seconds
            payload = None
            while process.is_alive():
                if receiver.poll(0.05):
                    payload = _receive_worker_message(receiver)
                    break
                if job.cancel.is_set():
                    worker_cancel.set()
                    process.join(self.cancel_grace_seconds)
                    if process.is_alive():
                        process.terminate()
                    break
                if time.monotonic() >= deadline:
                    worker_cancel.set()
                    process.join(self.cancel_grace_seconds)
                    if process.is_alive():
                        process.terminate()
                    break
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(2)
            if payload is None and receiver.poll():
                payload = _receive_worker_message(receiver)
            receiver.close()

            # Serialize terminal publication with cancel(). Whichever obtains this
            # lock first establishes the terminal outcome and its output invariant.
            with self._lock:
                current = self.store.load_state(job.run_id)
                if job.cancel.is_set() or current.status == "cancel_requested":
                    if current.status == "running":
                        self.store.transition(job.run_id, "cancel_requested")
                    self.store.transition(job.run_id, "cancelled")
                elif time.monotonic() >= deadline and payload is None:
                    self.store.transition(
                        job.run_id,
                        "failed",
                        failure_code="analysis_timeout",
                        failure_message="Analysis exceeded the local time limit. Analyze a shorter loop.",
                        retryable=True,
                    )
                elif not isinstance(payload, dict):
                    self.store.transition(
                        job.run_id,
                        "failed",
                        failure_code="worker_failed",
                        failure_message="The analysis worker stopped unexpectedly. Retry the run.",
                        retryable=True,
                    )
                elif payload.get("ok") is not True:
                    error = payload.get("error", {})
                    code = str(error.get("code", "worker_failed"))
                    message, retryable = _WORKER_ERRORS.get(code, _WORKER_ERRORS["worker_failed"])
                    self.store.transition(
                        job.run_id,
                        "failed",
                        failure_code=code if code in _WORKER_ERRORS else "worker_failed",
                        failure_message=message,
                        retryable=retryable,
                    )
                else:
                    timeline = timeline_from_mapping(payload["timeline"])
                    if (
                        timeline.timebase != spec.timebase
                        or timeline.analyzed_range != spec.analyzed_range
                    ):
                        raise AnalysisError(
                            "invalid_engine_output",
                            "The analysis worker returned a mismatched timebase.",
                            retryable=False,
                        )
                    self.store.publish_success(job.run_id, timeline)
        except AnalysisError as error:
            try:
                with self._lock:
                    current = self.store.load_state(job.run_id)
                    if current.status in {"queued", "running", "cancel_requested"}:
                        if job.cancel.is_set() or current.status == "cancel_requested":
                            if current.status == "running":
                                self.store.transition(job.run_id, "cancel_requested")
                            self.store.transition(job.run_id, "cancelled")
                        else:
                            self.store.transition(
                                job.run_id,
                                "failed",
                                failure_code=error.code,
                                failure_message=error.public_message,
                                retryable=error.retryable,
                            )
            except AnalysisError:
                pass
        except Exception:
            try:
                with self._lock:
                    current = self.store.load_state(job.run_id)
                    if current.status in {"queued", "running", "cancel_requested"}:
                        if job.cancel.is_set() or current.status == "cancel_requested":
                            if current.status == "running":
                                self.store.transition(job.run_id, "cancel_requested")
                            self.store.transition(job.run_id, "cancelled")
                        else:
                            self.store.transition(
                                job.run_id,
                                "failed",
                                failure_code="analysis_internal",
                                failure_message="Analysis failed safely. Retry the run.",
                                retryable=True,
                            )
            except AnalysisError:
                pass
        finally:
            process = job.process
            if process is not None and process.is_alive():
                process.terminate()
                process.join(2)
                if process.is_alive():
                    process.kill()
                    process.join(2)
            try:
                self.store.release_claim(job.run_id)
            except AnalysisError:
                pass


def _worker_main(sender, cancel_event, audio_path: str, spec_value: dict[str, Any]) -> None:
    try:
        spec = spec_from_mapping(spec_value)
        timeline = analyze_pcm16_wav(
            Path(audio_path),
            spec,
            cancelled=cancel_event.is_set,
        )
        _send_worker_message(sender, {"ok": True, "timeline": timeline.to_record_mapping()})
    except AnalysisError as error:
        _send_worker_message(sender, {"ok": False, "error": {"code": error.code}})
    except Exception:
        _send_worker_message(
            sender,
            {
                "ok": False,
                "error": {
                    "code": "worker_failed",
                },
            }
        )
    finally:
        sender.close()


def _send_worker_message(connection, value: dict[str, Any]) -> None:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(payload) > _MAX_WORKER_MESSAGE_BYTES:
        payload = b'{"error":{"code":"worker_failed"},"ok":false}'
    connection.send_bytes(payload)


def _receive_worker_message(connection) -> dict[str, Any]:
    try:
        payload = connection.recv_bytes(_MAX_WORKER_MESSAGE_BYTES)
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AnalysisError(
            "worker_protocol",
            "The analysis worker returned an invalid result. Retry the run.",
            retryable=True,
        ) from None
    if not isinstance(value, dict) or (
        value.get("ok") is not True and value.get("ok") is not False
    ):
        raise AnalysisError(
            "worker_protocol",
            "The analysis worker returned an invalid result. Retry the run.",
            retryable=True,
        )
    return value
