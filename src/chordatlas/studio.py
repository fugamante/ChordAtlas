from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from chordatlas.acquisition import (
    AcquisitionError,
    AcquisitionService,
    AcquisitionTransport,
)
from chordatlas.analysis import AnalysisError, AnalysisService
from chordatlas.media.models import FrameRange
from chordatlas.media.models import MediaImportError
from chordatlas.media.store import DEFAULT_MAX_UPLOAD_BYTES, ProjectMediaStore
from chordatlas.practice import PracticeError, PracticeService
from chordatlas.promotion import PromotionError, PromotionService
from chordatlas.promotion.service import public_preview
from chordatlas.review import ReviewError, ReviewService

_MAX_RANGE_HEADER = 256


class StudioServer(ThreadingHTTPServer):
    """Narrow loopback server; never use as a general file server."""

    daemon_threads = False
    block_on_close = True
    request_queue_size = 16

    def __init__(
        self,
        project_root: Path,
        *,
        port: int = 0,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        acquisition_transport: AcquisitionTransport | None = None,
    ) -> None:
        self.store = ProjectMediaStore.initialize(project_root)
        self.acquisition = AcquisitionService(
            project_root,
            transport=acquisition_transport,
            max_download_bytes=max_upload_bytes,
        )
        self.analysis = AnalysisService(project_root)
        self.review = ReviewService(project_root)
        self.promotion = PromotionService(project_root)
        self.practice = PracticeService(project_root)
        self.max_upload_bytes = max_upload_bytes
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.cookie_name = f"chordatlas_session_{secrets.token_hex(6)}"
        self.bootstrap_consumed = False
        self.session_lock = threading.Lock()
        self.import_slots = threading.BoundedSemaphore(2)
        self.lifecycle = threading.Condition()
        self.active_imports = 0
        self.accepting_imports = True
        self.playback_handles: dict[str, str] = {}
        self.playback_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), StudioHandler)
        host, bound_port = self.server_address
        if host != "127.0.0.1":
            self.server_close()
            raise RuntimeError("ChordAtlas Studio must bind to numeric IPv4 loopback")
        self.authority = f"127.0.0.1:{bound_port}"
        self.origin = f"http://{self.authority}"

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/#{self.bootstrap_token}"

    def playback_handle(self, source_id: str) -> str:
        with self.playback_lock:
            for handle, mapped_source in self.playback_handles.items():
                if mapped_source == source_id:
                    return handle
            handle = f"play_{secrets.token_urlsafe(24)}"
            self.playback_handles[handle] = source_id
            return handle

    def source_for_playback_handle(self, handle: str) -> str:
        with self.playback_lock:
            source_id = self.playback_handles.get(handle)
        if source_id is None:
            raise MediaImportError("unknown_media", "The playback asset is unavailable.")
        return source_id

    def begin_import(self) -> bool:
        with self.lifecycle:
            if not self.accepting_imports or not self.import_slots.acquire(blocking=False):
                return False
            self.active_imports += 1
            return True

    def finish_import(self) -> None:
        with self.lifecycle:
            self.active_imports -= 1
            self.import_slots.release()
            self.lifecycle.notify_all()

    def begin_shutdown(self) -> bool:
        with self.lifecycle:
            if (
                self.active_imports
                or self.acquisition.has_active()
                or self.analysis.has_active()
            ):
                return False
            self.accepting_imports = False
            return True

    def server_close(self) -> None:
        self.acquisition.close()
        self.analysis.close()
        super().server_close()


class StudioHandler(BaseHTTPRequestHandler):
    server: StudioServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, _format: str, *args) -> None:
        # Request targets may contain session-scoped media handles. Do not log them.
        return

    def handle_expect_100(self) -> bool:
        self._send_error(
            HTTPStatus.EXPECTATION_FAILED,
            "expectation_unsupported",
            "Expect: 100-continue is not supported.",
        )
        return False

    def do_GET(self) -> None:
        if not self._valid_host():
            return
        path = self._path()
        if path is None:
            return
        if path in {"/", "/index.html", "/app.js", "/style.css"}:
            self._serve_static(path)
            return
        if not self._authenticated():
            return
        if path == "/api/sources":
            self._send_json({"sources": self._public_sources()})
            return
        if path == "/api/acquisitions":
            self._send_json({"acquisitions": self.server.acquisition.list()})
            return
        if path == "/api/practice-history":
            self._serve_practice_usage()
            return
        if path.startswith("/api/acquisitions/"):
            self._serve_acquisition(path.removeprefix("/api/acquisitions/"))
            return
        if path.startswith("/api/sources/") and path.endswith("/analysis-runs"):
            source_id = path.removeprefix("/api/sources/").removesuffix("/analysis-runs")
            self._list_analysis_runs(source_id)
            return
        if path.startswith("/api/analysis-runs/") and path.endswith("/timeline"):
            run_id = path.removeprefix("/api/analysis-runs/").removesuffix("/timeline")
            self._serve_analysis_timeline(run_id)
            return
        if path.startswith("/api/analysis-runs/") and path.endswith("/review-sessions"):
            run_id = path.removeprefix("/api/analysis-runs/").removesuffix(
                "/review-sessions"
            )
            self._list_review_sessions(analysis_run_id=run_id)
            return
        if path.startswith("/api/sources/") and path.endswith("/review-sessions"):
            source_id = path.removeprefix("/api/sources/").removesuffix(
                "/review-sessions"
            )
            self._list_review_sessions(source_id=source_id)
            return
        if path.startswith("/api/sources/") and path.endswith("/practice-sessions"):
            source_id = path.removeprefix("/api/sources/").removesuffix(
                "/practice-sessions"
            )
            self._list_practice_sessions(source_id)
            return
        if path.startswith("/api/sources/") and path.endswith("/approvals"):
            source_id = path.removeprefix("/api/sources/").removesuffix("/approvals")
            self._list_active_approvals(source_id)
            return
        if path.startswith("/api/review-sessions/"):
            self._serve_review(path.removeprefix("/api/review-sessions/"))
            return
        if path.startswith("/api/approvals/") and path.endswith("/exports"):
            approval_id = path.removeprefix("/api/approvals/").removesuffix("/exports")
            self._serve_promotion_exports(approval_id)
            return
        if path.startswith("/api/practice-sessions/"):
            self._serve_practice(path.removeprefix("/api/practice-sessions/"))
            return
        if path.startswith("/api/approvals/"):
            self._serve_approval(path.removeprefix("/api/approvals/"))
            return
        if path.startswith("/api/analysis-runs/"):
            self._serve_analysis_status(path.removeprefix("/api/analysis-runs/"))
            return
        if path.startswith("/api/sources/") and path.endswith("/waveform"):
            source_id = path.removeprefix("/api/sources/").removesuffix("/waveform")
            self._serve_waveform(source_id)
            return
        if path.startswith("/media/"):
            self._serve_media(path.removeprefix("/media/"), head_only=False)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "The requested resource was not found.")

    def do_HEAD(self) -> None:
        if not self._valid_host():
            return
        path = self._path()
        if path is None:
            return
        if not self._authenticated():
            return
        if path.startswith("/media/"):
            self._serve_media(path.removeprefix("/media/"), head_only=True)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "The requested resource was not found.")

    def do_POST(self) -> None:
        if not self._valid_host():
            return
        path = self._path()
        if path is None:
            return
        if not self._same_origin_mutation():
            return
        if path == "/api/session":
            self._bootstrap_session()
            return
        if not self._authenticated():
            return
        if path == "/api/import":
            self._import_audio()
            return
        if path == "/api/acquisitions":
            self._start_acquisition()
            return
        if path.startswith("/api/acquisitions/") and path.endswith("/cancel"):
            run_id = path.removeprefix("/api/acquisitions/").removesuffix("/cancel")
            self._cancel_acquisition(run_id)
            return
        if path.startswith("/api/acquisitions/") and path.endswith("/retry"):
            run_id = path.removeprefix("/api/acquisitions/").removesuffix("/retry")
            self._retry_acquisition(run_id)
            return
        if path.startswith("/api/acquisitions/") and path.endswith("/forget"):
            run_id = path.removeprefix("/api/acquisitions/").removesuffix("/forget")
            self._forget_acquisition_locator(run_id)
            return
        if path.startswith("/api/sources/") and path.endswith("/analysis-runs"):
            source_id = path.removeprefix("/api/sources/").removesuffix("/analysis-runs")
            self._start_analysis(source_id)
            return
        if path.startswith("/api/analysis-runs/") and path.endswith("/cancel"):
            run_id = path.removeprefix("/api/analysis-runs/").removesuffix("/cancel")
            self._cancel_analysis(run_id)
            return
        if path.startswith("/api/analysis-runs/") and path.endswith("/retry"):
            run_id = path.removeprefix("/api/analysis-runs/").removesuffix("/retry")
            self._retry_analysis(run_id)
            return
        if path.startswith("/api/analysis-runs/") and path.endswith("/review-sessions"):
            run_id = path.removeprefix("/api/analysis-runs/").removesuffix(
                "/review-sessions"
            )
            self._start_review(run_id)
            return
        if path.startswith("/api/review-sessions/") and path.endswith("/edits"):
            session_id = path.removeprefix("/api/review-sessions/").removesuffix(
                "/edits"
            )
            self._apply_review_edit(session_id)
            return
        if path.startswith("/api/review-sessions/") and path.endswith("/undo"):
            session_id = path.removeprefix("/api/review-sessions/").removesuffix(
                "/undo"
            )
            self._navigate_review(session_id, direction="undo")
            return
        if path.startswith("/api/review-sessions/") and path.endswith("/redo"):
            session_id = path.removeprefix("/api/review-sessions/").removesuffix(
                "/redo"
            )
            self._navigate_review(session_id, direction="redo")
            return
        if path.startswith("/api/review-sessions/") and path.endswith("/promotion-preview"):
            session_id = path.removeprefix("/api/review-sessions/").removesuffix(
                "/promotion-preview"
            )
            self._preview_promotion(session_id)
            return
        if path.startswith("/api/review-sessions/") and path.endswith("/approvals"):
            session_id = path.removeprefix("/api/review-sessions/").removesuffix(
                "/approvals"
            )
            self._approve_promotion(session_id)
            return
        if path.startswith("/api/approvals/") and path.endswith("/revoke"):
            approval_id = path.removeprefix("/api/approvals/").removesuffix("/revoke")
            self._revoke_approval(approval_id)
            return
        if path.startswith("/api/approvals/") and path.endswith("/practice-sessions"):
            approval_id = path.removeprefix("/api/approvals/").removesuffix(
                "/practice-sessions"
            )
            self._start_practice(approval_id)
            return
        if path.startswith("/api/practice-sessions/") and path.endswith("/attempts"):
            practice_session_id = path.removeprefix(
                "/api/practice-sessions/"
            ).removesuffix("/attempts")
            self._save_practice(practice_session_id)
            return
        if path == "/api/practice-history/reset":
            self._reset_practice_history()
            return
        if path == "/api/shutdown":
            if not self.server.begin_shutdown():
                self._send_error(
                    HTTPStatus.CONFLICT,
                    "import_busy",
                    "Wait for the active import, acquisition, or analysis to finish, then retry shutdown.",
                )
                return
            self._send_json({"status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "The requested resource was not found.")

    def do_OPTIONS(self) -> None:
        self._send_error(HTTPStatus.FORBIDDEN, "cross_origin_forbidden", "Cross-origin access is disabled.")

    def _bootstrap_session(self) -> None:
        tokens = self.headers.get_all("X-ChordAtlas-Bootstrap", failobj=[])
        if len(tokens) != 1:
            self._send_error(HTTPStatus.FORBIDDEN, "invalid_bootstrap", "Studio launch expired.")
            return
        with self.server.session_lock:
            if self.server.bootstrap_consumed or not secrets.compare_digest(
                tokens[0],
                self.server.bootstrap_token,
            ):
                self._send_error(HTTPStatus.FORBIDDEN, "invalid_bootstrap", "Studio launch expired.")
                return
            self.server.bootstrap_consumed = True
        self._send_json(
            {"status": "ready"},
            extra_headers={
                "Set-Cookie": (
                    f"{self.server.cookie_name}={self.server.session_token}; "
                    "HttpOnly; SameSite=Strict; Path=/"
                )
            },
        )

    def _import_audio(self) -> None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "unsupported_transfer",
                "Chunked uploads are not supported.",
            )
            return
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1:
            self._send_error(
                HTTPStatus.LENGTH_REQUIRED,
                "content_length_required",
                "A single Content-Length header is required.",
            )
            return
        try:
            byte_length = int(lengths[0])
        except ValueError:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_content_length",
                "Content-Length must be an integer.",
            )
            return
        if byte_length <= 0 or byte_length > self.server.max_upload_bytes:
            self._send_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "upload_size",
                "The upload is empty or exceeds the configured limit.",
            )
            return
        if self.headers.get_content_type() not in {"audio/wav", "application/octet-stream"}:
            self._send_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_content_type",
                "Upload a RIFF/WAVE file.",
            )
            return
        names = self.headers.get_all("X-ChordAtlas-File-Name", failobj=[])
        if len(names) != 1 or len(names[0]) > 768:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_file_name",
                "The upload filename is missing or too long.",
            )
            return
        authorized = self.headers.get("X-ChordAtlas-Authorized") == "true"
        if not self.server.begin_import():
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "import_busy",
                "Two imports are already running. Retry shortly.",
            )
            return
        try:
            source, asset = self.server.store.import_stream(
                self.rfile,
                byte_length=byte_length,
                display_name=unquote(names[0]),
                authorization_confirmed=authorized,
                max_upload_bytes=self.server.max_upload_bytes,
            )
            handle = self.server.playback_handle(source.id)
            self._send_json(
                {
                    "source": source.to_public_mapping(),
                    "asset": asset.to_public_mapping(),
                    "waveform_url": f"/api/sources/{source.id}/waveform",
                    "playback_url": f"/media/{handle}",
                },
                status=HTTPStatus.CREATED,
            )
        except MediaImportError as error:
            self._send_json(
                error.to_mapping(),
                status=HTTPStatus.BAD_REQUEST if error.retryable else HTTPStatus.CONFLICT,
            )
        except (OSError, socket.timeout):
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "import_failed",
                "The import failed without changing existing assets. Retry the operation.",
            )
        finally:
            self.server.finish_import()

    def _serve_waveform(self, source_id: str) -> None:
        try:
            waveform = self.server.store.waveform_for_source(source_id)
            self._send_json(waveform.to_mapping())
        except MediaImportError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _serve_acquisition(self, run_id: str) -> None:
        try:
            self._send_json(
                {"acquisition": self._public_acquisition(self.server.acquisition.status(run_id))}
            )
        except AcquisitionError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _start_acquisition(self) -> None:
        try:
            if self.headers.get_content_type() != "application/json":
                raise AcquisitionError(
                    "unsupported_content_type",
                    "Acquisition requests require application/json.",
                    retryable=False,
                )
            try:
                value = self._read_json_body(max_bytes=8192)
            except AnalysisError as error:
                raise AcquisitionError(error.code, error.public_message) from None
            if set(value) != {"url", "display_name", "authorization_confirmed"}:
                raise AcquisitionError(
                    "invalid_request",
                    "Acquisition accepts a URL, display name, and authorization confirmation.",
                    retryable=False,
                )
            run_id = self.server.acquisition.start(
                url=value["url"],
                display_name=value["display_name"],
                authorization_confirmed=value["authorization_confirmed"] is True,
                idempotency_key=self._acquisition_idempotency_key(),
            )
            self._send_json(
                {
                    "acquisition": self._public_acquisition(
                        self.server.acquisition.status(run_id)
                    )
                },
                status=HTTPStatus.ACCEPTED,
            )
        except (AcquisitionError, TypeError) as error:
            if isinstance(error, TypeError):
                error = AcquisitionError(
                    "invalid_request",
                    "Acquisition fields have invalid types.",
                    retryable=False,
                )
            self._send_acquisition_error(error)

    def _cancel_acquisition(self, run_id: str) -> None:
        try:
            value = self._read_acquisition_empty_body()
            if value:
                raise AcquisitionError(
                    "invalid_request",
                    "Cancellation accepts an empty JSON object.",
                    retryable=False,
                )
            self._send_json(
                {
                    "acquisition": self._public_acquisition(
                        self.server.acquisition.cancel(run_id)
                    )
                }
            )
        except AcquisitionError as error:
            self._send_acquisition_error(error)

    def _retry_acquisition(self, run_id: str) -> None:
        try:
            value = self._read_acquisition_empty_body()
            if value:
                raise AcquisitionError(
                    "invalid_request",
                    "Retry accepts an empty JSON object.",
                    retryable=False,
                )
            next_id = self.server.acquisition.retry(
                run_id,
                idempotency_key=self._acquisition_idempotency_key(),
            )
            self._send_json(
                {
                    "acquisition": self._public_acquisition(
                        self.server.acquisition.status(next_id)
                    )
                },
                status=HTTPStatus.ACCEPTED,
            )
        except AcquisitionError as error:
            self._send_acquisition_error(error)

    def _forget_acquisition_locator(self, run_id: str) -> None:
        try:
            value = self._read_acquisition_empty_body()
            if value:
                raise AcquisitionError(
                    "invalid_request",
                    "Locator removal accepts an empty JSON object.",
                    retryable=False,
                )
            self._send_json(
                {
                    "acquisition": self._public_acquisition(
                        self.server.acquisition.forget_locator(run_id)
                    )
                }
            )
        except AcquisitionError as error:
            self._send_acquisition_error(error)

    def _public_acquisition(self, value: dict[str, Any]) -> dict[str, Any]:
        if value["status"] != "succeeded":
            return value
        source_id = str(value["source_id"])
        source = self.server.store.source(source_id)
        asset = self.server.store.asset_for_source(source_id)
        handle = self.server.playback_handle(source_id)
        return {
            **value,
            "result": {
                "source": source.to_public_mapping(),
                "asset": asset.to_public_mapping(),
                "waveform_url": f"/api/sources/{source_id}/waveform",
                "playback_url": f"/media/{handle}",
            },
        }

    def _read_acquisition_empty_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise AcquisitionError(
                "unsupported_content_type",
                "Acquisition requests require application/json.",
                retryable=False,
            )
        try:
            return self._read_json_body(max_bytes=64)
        except AnalysisError as error:
            raise AcquisitionError(error.code, error.public_message) from None

    def _acquisition_idempotency_key(self) -> str:
        values = self.headers.get_all("Idempotency-Key", failobj=[])
        if len(values) != 1:
            raise AcquisitionError(
                "invalid_idempotency_key",
                "Use exactly one Idempotency-Key header.",
                retryable=False,
            )
        return values[0]

    def _send_acquisition_error(self, error: AcquisitionError) -> None:
        if error.code == "unknown_acquisition":
            status = HTTPStatus.NOT_FOUND
        elif error.code in {"acquisition_busy", "idempotency_conflict"}:
            status = HTTPStatus.CONFLICT
        elif error.code in {"request_too_large", "download_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        else:
            status = HTTPStatus.BAD_REQUEST
        self._send_json(error.to_mapping(), status=status)

    def _list_analysis_runs(self, source_id: str) -> None:
        try:
            self.server.store.source(source_id)
            self._send_json({"runs": self.server.analysis.list(source_id=source_id)})
        except (MediaImportError, AnalysisError) as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _serve_analysis_status(self, run_id: str) -> None:
        try:
            self._send_json({"run": self.server.analysis.status(run_id)})
        except AnalysisError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _serve_analysis_timeline(self, run_id: str) -> None:
        try:
            self._send_json({"timeline": self.server.analysis.timeline(run_id)})
        except AnalysisError as error:
            status = HTTPStatus.CONFLICT if error.code == "timeline_unavailable" else HTTPStatus.NOT_FOUND
            self._send_json(error.to_mapping(), status=status)

    def _start_analysis(self, source_id: str) -> None:
        try:
            value = self._read_json_body(max_bytes=4096)
            if set(value) != {"range"}:
                raise AnalysisError(
                    "invalid_request",
                    "Analysis accepts exactly one range field.",
                    retryable=False,
                )
            scope = value.get("range")
            frame_range = None
            if scope is not None:
                if not isinstance(scope, dict) or set(scope) != {"start_frame", "end_frame"}:
                    raise AnalysisError("invalid_analysis_range", "Analysis range is invalid.")
                start_frame = scope["start_frame"]
                end_frame = scope["end_frame"]
                if (
                    not isinstance(start_frame, int)
                    or isinstance(start_frame, bool)
                    or not isinstance(end_frame, int)
                    or isinstance(end_frame, bool)
                ):
                    raise AnalysisError("invalid_analysis_range", "Analysis range is invalid.")
                frame_range = FrameRange(
                    start_frame,
                    end_frame,
                )
            keys = self.headers.get_all("Idempotency-Key", failobj=[])
            if len(keys) > 1:
                raise AnalysisError("invalid_idempotency_key", "Use one Idempotency-Key header.")
            run_id = self.server.analysis.start(
                source_id=source_id,
                frame_range=frame_range,
                idempotency_key=keys[0] if keys else None,
            )
            self._send_json(
                {"run": self.server.analysis.status(run_id)},
                status=HTTPStatus.ACCEPTED,
            )
        except (KeyError, TypeError, ValueError):
            error = AnalysisError("invalid_analysis_range", "Analysis range is invalid.")
            self._send_json(error.to_mapping(), status=HTTPStatus.BAD_REQUEST)
        except (MediaImportError, AnalysisError) as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"analysis_busy", "analysis_closed"}
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json(error.to_mapping(), status=status)

    def _cancel_analysis(self, run_id: str) -> None:
        try:
            value = self._read_json_body(max_bytes=64)
            if value:
                raise AnalysisError("invalid_request", "Cancel does not accept request fields.")
            self._send_json({"run": self.server.analysis.cancel(run_id)})
        except AnalysisError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.CONFLICT)

    def _retry_analysis(self, run_id: str) -> None:
        try:
            value = self._read_json_body(max_bytes=64)
            if value:
                raise AnalysisError("invalid_request", "Retry does not accept request fields.")
            keys = self.headers.get_all("Idempotency-Key", failobj=[])
            if len(keys) > 1:
                raise AnalysisError("invalid_idempotency_key", "Use one Idempotency-Key header.")
            retry_id = self.server.analysis.retry(
                run_id,
                idempotency_key=keys[0] if keys else None,
            )
            self._send_json(
                {"run": self.server.analysis.status(retry_id)},
                status=HTTPStatus.ACCEPTED,
            )
        except AnalysisError as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"analysis_busy", "analysis_closed", "retry_unavailable"}
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json(error.to_mapping(), status=status)

    def _list_review_sessions(
        self,
        *,
        source_id: str | None = None,
        analysis_run_id: str | None = None,
    ) -> None:
        try:
            self._send_json(
                {
                    "sessions": self.server.review.list(
                        source_id=source_id,
                        analysis_run_id=analysis_run_id,
                    )
                }
            )
        except ReviewError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _serve_review(self, session_id: str) -> None:
        try:
            value = self.server.review.get(session_id)
            self._send_json(
                {"review": value},
                extra_headers={"ETag": f'"{value["head"]["token"]}"'},
            )
        except ReviewError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)

    def _start_review(self, run_id: str) -> None:
        try:
            value = self._read_review_json(max_bytes=64)
            if value:
                raise ReviewError("invalid_request", "Start review accepts no fields.")
            result = self.server.review.create(
                run_id,
                idempotency_key=self._idempotency_key(),
            )
            self._send_json(
                {"review": result},
                status=HTTPStatus.CREATED,
                extra_headers={"ETag": f'"{result["head"]["token"]}"'},
            )
        except (AnalysisError, ReviewError) as error:
            self._send_review_error(error)

    def _apply_review_edit(self, session_id: str) -> None:
        try:
            value = self._read_review_json(max_bytes=8 * 1024)
            if set(value) != {"kind", "parameters"}:
                raise ReviewError("invalid_request", "Review edit fields are invalid.")
            if not isinstance(value["kind"], str) or not isinstance(
                value["parameters"], dict
            ):
                raise ReviewError("invalid_request", "Review edit fields are invalid.")
            result = self.server.review.apply(
                session_id,
                expected_token=self._review_precondition(),
                idempotency_key=self._idempotency_key(),
                kind=value["kind"],
                parameters=value["parameters"],
            )
            self._send_json(
                {"review": result},
                extra_headers={"ETag": f'"{result["head"]["token"]}"'},
            )
        except ReviewError as error:
            self._send_review_error(error)

    def _navigate_review(self, session_id: str, *, direction: str) -> None:
        try:
            value = self._read_review_json(max_bytes=512)
            expected = self._review_precondition()
            key = self._idempotency_key()
            if direction == "undo":
                if value:
                    raise ReviewError("invalid_request", "Undo accepts no request fields.")
                result = self.server.review.undo(
                    session_id,
                    expected_token=expected,
                    idempotency_key=key,
                )
            else:
                if set(value) != {"revision_id"} or not isinstance(
                    value["revision_id"], str
                ):
                    raise ReviewError(
                        "invalid_request",
                        "Redo requires one revision identifier.",
                    )
                result = self.server.review.redo(
                    session_id,
                    expected_token=expected,
                    idempotency_key=key,
                    requested_revision_id=value["revision_id"],
                )
            self._send_json(
                {"review": result},
                extra_headers={"ETag": f'"{result["head"]["token"]}"'},
            )
        except ReviewError as error:
            self._send_review_error(error)

    def _preview_promotion(self, session_id: str) -> None:
        try:
            value = self._read_promotion_json(max_bytes=128 * 1024)
            if set(value) != {"revision_id", "mapping"} or not isinstance(
                value["revision_id"], str
            ) or not isinstance(value["mapping"], dict):
                raise PromotionError(
                    "invalid_request",
                    "Promotion preview fields are invalid.",
                )
            preview = self.server.promotion.preview(
                session_id,
                value["revision_id"],
                expected_head_token=self._review_precondition(),
                config_mapping=value["mapping"],
            )
            self._send_json({"preview": public_preview(preview)})
        except (PromotionError, ReviewError) as error:
            self._send_promotion_error(error)

    def _approve_promotion(self, session_id: str) -> None:
        try:
            value = self._read_promotion_json(max_bytes=128 * 1024)
            if (
                set(value)
                != {
                    "revision_id",
                    "mapping",
                    "expected_spec_id",
                    "expected_result_id",
                    "expected_issue_digest",
                    "acknowledged_issue_ids",
                }
                or not isinstance(value["revision_id"], str)
                or not isinstance(value["mapping"], dict)
                or not isinstance(value["expected_spec_id"], str)
                or not isinstance(value["expected_result_id"], str)
                or not isinstance(value["expected_issue_digest"], str)
                or not isinstance(value["acknowledged_issue_ids"], list)
                or any(
                    not isinstance(item, str)
                    for item in value["acknowledged_issue_ids"]
                )
            ):
                raise PromotionError(
                    "invalid_request",
                    "Promotion approval fields are invalid.",
                )
            result = self.server.promotion.approve(
                session_id,
                value["revision_id"],
                expected_head_token=self._review_precondition(),
                idempotency_key=self._idempotency_key(),
                config_mapping=value["mapping"],
                expected_spec_id=value["expected_spec_id"],
                expected_result_id=value["expected_result_id"],
                expected_issue_digest=value["expected_issue_digest"],
                acknowledged_issue_ids=tuple(value["acknowledged_issue_ids"]),
            )
            token = result["approval"]["token"]
            self._send_json(
                result,
                status=HTTPStatus.CREATED,
                extra_headers={} if token is None else {"ETag": f'"{token}"'},
            )
        except (PromotionError, ReviewError) as error:
            self._send_promotion_error(error)

    def _serve_approval(self, approval_id: str) -> None:
        try:
            result = self.server.promotion.get(approval_id)
            token = result["approval"]["token"]
            self._send_json(
                result,
                extra_headers={} if token is None else {"ETag": f'"{token}"'},
            )
        except PromotionError as error:
            self._send_promotion_error(error)

    def _serve_promotion_exports(self, approval_id: str) -> None:
        try:
            self._send_json({"exports": self.server.promotion.exports(approval_id)})
        except PromotionError as error:
            self._send_promotion_error(error)

    def _revoke_approval(self, approval_id: str) -> None:
        try:
            value = self._read_promotion_json(max_bytes=2 * 1024)
            if set(value) != {"reason"} or not isinstance(value["reason"], str):
                raise PromotionError(
                    "invalid_request",
                    "Revocation requires one reason.",
                )
            result = self.server.promotion.revoke(
                approval_id,
                expected_token=self._review_precondition(),
                idempotency_key=self._idempotency_key(),
                reason=value["reason"],
            )
            self._send_json(result)
        except (PromotionError, ReviewError) as error:
            self._send_promotion_error(error)

    def _list_practice_sessions(self, source_id: str) -> None:
        try:
            self.server.store.source(source_id)
            values = [
                self._practice_private_status(item)
                for item in self.server.practice.list(source_id=source_id)
            ]
            self._send_json({"practice_sessions": values})
        except (PracticeError, MediaImportError) as error:
            self._send_practice_error(error)

    def _serve_practice_usage(self) -> None:
        try:
            usage = self.server.practice.usage()
            self._send_json(
                {"practice_history": usage},
                extra_headers={
                    "Cache-Control": "no-store",
                    "ETag": f'"{usage["token"]}"',
                },
            )
        except PracticeError as error:
            self._send_practice_error(error)

    def _reset_practice_history(self) -> None:
        try:
            value = self._read_practice_json(max_bytes=1024)
            if set(value) != {"confirmation"} or not isinstance(
                value["confirmation"], str
            ):
                raise PracticeError(
                    "invalid_confirmation",
                    "Practice-history reset requires the exact confirmation phrase.",
                )
            result = self.server.practice.reset(
                expected_token=self._practice_precondition(),
                idempotency_key=self._practice_idempotency_key(),
                confirmation=value["confirmation"],
            )
            usage = result["usage"]
            self._send_json(
                {"practice_history_reset": result},
                extra_headers={
                    "Cache-Control": "no-store",
                    "ETag": f'"{usage["token"]}"',
                },
            )
        except PracticeError as error:
            self._send_practice_error(error)

    def _list_active_approvals(self, source_id: str) -> None:
        try:
            self.server.store.source(source_id)
            values = self.server.promotion.list_active_for_source(source_id)
            self._send_json({"approvals": [item["approval"] for item in values]})
        except MediaImportError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)
        except PromotionError as error:
            self._send_promotion_error(error)

    def _serve_practice(self, practice_session_id: str) -> None:
        try:
            value = self._practice_private_status(
                self.server.practice.get(practice_session_id)
            )
            self._send_json(
                {"practice": value},
                extra_headers={"ETag": f'"{value["head"]["token"]}"'},
            )
        except PracticeError as error:
            self._send_practice_error(error)

    def _start_practice(self, approval_id: str) -> None:
        try:
            value = self._practice_private_status(
                self.server.practice.create(
                    approval_id,
                    idempotency_key=self._practice_idempotency_key(),
                )
            )
            self._send_json(
                {"practice": value},
                status=HTTPStatus.CREATED,
                extra_headers={"ETag": f'"{value["head"]["token"]}"'},
            )
        except PracticeError as error:
            self._send_practice_error(error)

    def _save_practice(self, practice_session_id: str) -> None:
        try:
            value = self._practice_private_status(
                self.server.practice.update(
                    practice_session_id,
                    expected_token=self._practice_precondition(),
                    idempotency_key=self._practice_idempotency_key(),
                    state_mapping=self._read_practice_json(max_bytes=16 * 1024),
                )
            )
            self._send_json(
                {"practice": value},
                extra_headers={"ETag": f'"{value["head"]["token"]}"'},
            )
        except PracticeError as error:
            self._send_practice_error(error)

    def _practice_private_status(self, value: dict[str, Any]) -> dict[str, Any]:
        with self.server.practice.store.lifecycle():
            source_id = self.server.practice.store.load_session(
                value["practice_session"]["practice_session_id"]
            ).source_id
        remote = [
            item
            for item in self.server.acquisition.list()
            if item["source_id"] == source_id and item["status"] == "succeeded"
        ]
        if not remote:
            source_state = "local_authorized"
            message = "Authorized local file; project media remains private."
        elif any(item["locator_retained"] for item in remote):
            source_state = "direct_https_authorized"
            message = "Authorized direct HTTPS source; private locator retained locally."
        else:
            source_state = "remote_locator_forgotten"
            message = "Direct HTTPS locator forgotten; validated local media remains available."
        return {
            **value,
            "source_authorization": {
                "status": source_state,
                "message": message,
            },
        }

    def _read_practice_json(self, *, max_bytes: int) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise PracticeError(
                "unsupported_content_type",
                "Practice requests require application/json.",
            )
        if self.headers.get("Transfer-Encoding") is not None:
            raise PracticeError(
                "unsupported_transfer",
                "Chunked practice requests are not supported.",
            )
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1:
            raise PracticeError(
                "content_length_required",
                "A single Content-Length is required.",
            )
        if re.fullmatch(r"[0-9]+", lengths[0]) is None:
            raise PracticeError(
                "invalid_content_length",
                "Content-Length must be an integer.",
            )
        length = int(lengths[0])
        if not 0 <= length <= max_bytes:
            raise PracticeError(
                "request_too_large",
                "The practice request is too large.",
            )
        body = self.rfile.read(length)
        if len(body) != length:
            raise PracticeError(
                "invalid_json",
                "The practice request body is truncated.",
            )
        try:
            value = json.loads(
                body or b"{}",
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeError, ValueError):
            raise PracticeError(
                "invalid_json",
                "The practice request must be valid JSON.",
            ) from None
        if not isinstance(value, dict):
            raise PracticeError(
                "invalid_json",
                "The practice request must be a JSON object.",
            )
        return value

    def _practice_idempotency_key(self) -> str:
        values = self.headers.get_all("Idempotency-Key", failobj=[])
        if len(values) != 1:
            raise PracticeError(
                "invalid_idempotency_key",
                "Use exactly one Idempotency-Key header.",
            )
        return values[0]

    def _practice_precondition(self) -> str:
        values = self.headers.get_all("If-Match", failobj=[])
        if len(values) != 1:
            raise PracticeError(
                "precondition_required",
                "Practice changes require the current strong ETag.",
            )
        value = values[0]
        if (
            len(value) < 3
            or not value.startswith('"')
            or not value.endswith('"')
            or value.startswith("W/")
            or value == '"*"'
        ):
            raise PracticeError(
                "precondition_required",
                "Practice changes require the current strong ETag.",
            )
        return value[1:-1]

    def _send_practice_error(self, error) -> None:
        if error.code == "precondition_required":
            status = HTTPStatus.PRECONDITION_REQUIRED
        elif error.code in {
            "practice_conflict",
            "idempotency_conflict",
            "idempotency_expired",
            "practice_history_changed",
        }:
            status = HTTPStatus.PRECONDITION_FAILED
        elif error.code in {
            "practice_busy",
            "approval_revoked",
            "review_changed",
            "practice_cleanup_incomplete",
        }:
            status = HTTPStatus.CONFLICT
        elif error.code in {"practice_unavailable", "media_unavailable"}:
            status = HTTPStatus.NOT_FOUND
        elif error.code in {"practice_limit", "request_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        else:
            status = HTTPStatus.BAD_REQUEST
        self._send_json(error.to_mapping(), status=status)

    def _read_promotion_json(self, *, max_bytes: int) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise PromotionError(
                "unsupported_content_type",
                "Promotion requests require application/json.",
            )
        try:
            return self._read_json_body(max_bytes=max_bytes)
        except AnalysisError as error:
            raise PromotionError(error.code, error.public_message) from None

    def _send_promotion_error(self, error) -> None:
        if error.code in {
            "review_conflict",
            "promotion_conflict",
            "preview_mismatch",
            "active_approval_exists",
            "idempotency_conflict",
        }:
            status = HTTPStatus.PRECONDITION_FAILED
        elif error.code in {
            "promotion_unavailable",
            "review_unavailable",
        }:
            status = HTTPStatus.NOT_FOUND
        elif error.code in {"promotion_busy"}:
            status = HTTPStatus.CONFLICT
        elif error.code in {"promotion_limit", "request_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        else:
            status = HTTPStatus.BAD_REQUEST
        self._send_json(error.to_mapping(), status=status)

    def _read_review_json(self, *, max_bytes: int) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ReviewError(
                "unsupported_content_type",
                "Review requests require application/json.",
            )
        try:
            return self._read_json_body(max_bytes=max_bytes)
        except AnalysisError as error:
            raise ReviewError(error.code, error.public_message) from None

    def _idempotency_key(self) -> str:
        values = self.headers.get_all("Idempotency-Key", failobj=[])
        if len(values) != 1:
            raise ReviewError(
                "invalid_idempotency_key",
                "Use exactly one Idempotency-Key header.",
            )
        return values[0]

    def _review_precondition(self) -> str:
        values = self.headers.get_all("If-Match", failobj=[])
        if len(values) != 1:
            raise ReviewError(
                "precondition_required",
                "Review changes require the current strong ETag.",
            )
        value = values[0]
        if (
            len(value) < 3
            or not value.startswith('"')
            or not value.endswith('"')
            or value.startswith('W/')
            or value == '"*"'
        ):
            raise ReviewError(
                "precondition_required",
                "Review changes require the current strong ETag.",
            )
        return value[1:-1]

    def _send_review_error(self, error) -> None:
        if error.code == "precondition_required":
            status = HTTPStatus.PRECONDITION_REQUIRED
        elif error.code in {
            "review_precondition_failed",
            "idempotency_conflict",
            "redo_conflict",
            "move_collision",
            "merge_incompatible",
        }:
            status = HTTPStatus.PRECONDITION_FAILED
        elif error.code in {"review_busy", "review_ready"}:
            status = HTTPStatus.CONFLICT
        elif error.code in {"review_unavailable", "invalid_review_id"}:
            status = HTTPStatus.NOT_FOUND
        elif error.code in {"review_limit", "request_too_large"}:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        else:
            status = HTTPStatus.BAD_REQUEST
        self._send_json(error.to_mapping(), status=status)

    def _read_json_body(self, *, max_bytes: int) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise AnalysisError("unsupported_transfer", "Chunked requests are not supported.")
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1:
            raise AnalysisError("content_length_required", "A single Content-Length is required.")
        if re.fullmatch(r"[0-9]+", lengths[0]) is None:
            raise AnalysisError("invalid_content_length", "Content-Length must be an integer.")
        length = int(lengths[0])
        if not 0 <= length <= max_bytes:
            raise AnalysisError("request_too_large", "The analysis request is too large.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise AnalysisError("invalid_json", "The request body is truncated.")
        try:
            value = json.loads(
                body or b"{}",
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeError, ValueError):
            raise AnalysisError("invalid_json", "The analysis request must be valid JSON.") from None
        if not isinstance(value, dict):
            raise AnalysisError("invalid_json", "The analysis request must be a JSON object.")
        return value

    def _serve_media(self, handle: str, *, head_only: bool) -> None:
        if len(handle) > 128 or not handle.startswith("play_"):
            self._send_error(HTTPStatus.NOT_FOUND, "unknown_media", "The playback asset is unavailable.")
            return
        try:
            source_id = self.server.source_for_playback_handle(handle)
            _asset, handle_file = self.server.store.open_audio_for_source(source_id)
        except MediaImportError as error:
            self._send_json(error.to_mapping(), status=HTTPStatus.NOT_FOUND)
            return
        with handle_file:
            size = os.fstat(handle_file.fileno()).st_size
            try:
                start, end, partial = _parse_range(self.headers.get("Range"), size)
            except ValueError:
                self._send_headers(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    content_length=0,
                    content_type="audio/wav",
                    extra_headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
                )
                return
            length = end - start + 1
            headers = {
                "Accept-Ranges": "bytes",
                "ETag": f'"{source_id}"',
            }
            if partial:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send_headers(
                HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK,
                content_length=length,
                content_type="audio/wav",
                extra_headers=headers,
            )
            if head_only:
                return
            handle_file.seek(start)
            remaining = length
            while remaining:
                payload = handle_file.read(min(64 * 1024, remaining))
                if not payload:
                    break
                self.wfile.write(payload)
                remaining -= len(payload)

    def _public_sources(self) -> list[dict[str, Any]]:
        remote_source_ids = {
            str(item["source_id"])
            for item in self.server.acquisition.list()
            if item["status"] == "succeeded"
        }
        values = []
        for item in self.server.store.list_public_sources():
            source_id = str(item["source"]["id"])
            handle = self.server.playback_handle(source_id)
            values.append(
                {
                    **item,
                    "source_kind": (
                        "direct_https_wav"
                        if source_id in remote_source_ids
                        else "local_wav"
                    ),
                    "waveform_url": f"/api/sources/{source_id}/waveform",
                    "playback_url": f"/media/{handle}",
                }
            )
        return values

    def _serve_static(self, path: str) -> None:
        name = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
        }[path]
        content_type = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "text/javascript; charset=utf-8",
            "style.css": "text/css; charset=utf-8",
        }[name]
        payload = resources.files("chordatlas.studio_assets").joinpath(name).read_bytes()
        self._send_headers(
            HTTPStatus.OK,
            content_length=len(payload),
            content_type=content_type,
        )
        self.wfile.write(payload)

    def _path(self) -> str | None:
        parsed = urlsplit(self.path)
        if parsed.query:
            self._send_error(HTTPStatus.BAD_REQUEST, "query_forbidden", "Query strings are not accepted.")
            return None
        return parsed.path

    def _valid_host(self) -> bool:
        hosts = self.headers.get_all("Host", failobj=[])
        if len(hosts) != 1 or hosts[0] != self.server.authority:
            self._send_error(HTTPStatus.FORBIDDEN, "invalid_host", "The request host is not allowed.")
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site == "cross-site":
            self._send_error(
                HTTPStatus.FORBIDDEN,
                "cross_origin_forbidden",
                "Cross-origin access is disabled.",
            )
            return False
        return True

    def _same_origin_mutation(self) -> bool:
        origins = self.headers.get_all("Origin", failobj=[])
        if len(origins) != 1 or origins[0] != self.server.origin:
            self._send_error(
                HTTPStatus.FORBIDDEN,
                "invalid_origin",
                "State changes require the Studio origin.",
            )
            return False
        if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin"}:
            self._send_error(
                HTTPStatus.FORBIDDEN,
                "cross_origin_forbidden",
                "Cross-origin access is disabled.",
            )
            return False
        return True

    def _authenticated(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            self._send_error(HTTPStatus.FORBIDDEN, "invalid_session", "Studio session is invalid.")
            return False
        morsel = cookie.get(self.server.cookie_name)
        if morsel is None or not secrets.compare_digest(
            morsel.value,
            self.server.session_token,
        ):
            self._send_error(HTTPStatus.FORBIDDEN, "invalid_session", "Studio session is invalid.")
            return False
        return True

    def _send_json(
        self,
        value: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
        self._send_headers(
            status,
            content_length=len(payload),
            content_type="application/json; charset=utf-8",
            extra_headers=extra_headers,
        )
        self.wfile.write(payload)

    def _send_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        self._send_json(
            {"error": {"code": code, "message": message, "retryable": False}},
            status=status,
        )

    def _send_headers(
        self,
        status: HTTPStatus,
        *,
        content_length: int,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _content_security_policy())
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.close_connection = True


def _reject_duplicate_json_keys(pairs):
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def create_server(
    project_root: Path,
    *,
    port: int = 0,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    acquisition_transport: AcquisitionTransport | None = None,
) -> StudioServer:
    return StudioServer(
        project_root,
        port=port,
        max_upload_bytes=max_upload_bytes,
        acquisition_transport=acquisition_transport,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chordatlas-studio",
        description="Run the local-only ChordAtlas audio workspace.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Existing project directory that will own private .chordatlas media storage.",
    )
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects a free port.")
    parser.add_argument(
        "--max-upload-mib",
        type=int,
        default=DEFAULT_MAX_UPLOAD_BYTES // (1024 * 1024),
        help="Maximum accepted WAV size in MiB.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the launch URL without opening the default browser.",
    )
    args = parser.parse_args(argv)
    if not (0 <= args.port <= 65535):
        parser.error("--port must be between 0 and 65535")
    if args.max_upload_mib <= 0 or args.max_upload_mib > 2048:
        parser.error("--max-upload-mib must be between 1 and 2048")
    try:
        server = create_server(
            args.project,
            port=args.port,
            max_upload_bytes=args.max_upload_mib * 1024 * 1024,
        )
    except (OSError, MediaImportError) as error:
        message = error.public_message if isinstance(error, MediaImportError) else str(error)
        print(f"Unable to start ChordAtlas Studio: {message}", file=sys.stderr)
        return 1

    print(f"ChordAtlas Studio: {server.launch_url}", file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(server.launch_url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _parse_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    if len(value) > _MAX_RANGE_HEADER or not value.startswith("bytes="):
        raise ValueError("invalid range")
    spec = value.removeprefix("bytes=")
    if "," in spec or "-" not in spec:
        raise ValueError("multiple or invalid ranges are unsupported")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        if not end_text.isdigit():
            raise ValueError("invalid suffix range")
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        start = max(size - suffix, 0)
        end = size - 1
    else:
        if not start_text.isdigit() or (end_text and not end_text.isdigit()):
            raise ValueError("invalid range")
        start = int(start_text)
        end = size - 1 if not end_text else int(end_text)
        if start >= size or end < start:
            raise ValueError("unsatisfiable range")
        end = min(end, size - 1)
    return start, end, True


def _content_security_policy() -> str:
    return (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "media-src 'self'; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )


if __name__ == "__main__":
    raise SystemExit(main())
