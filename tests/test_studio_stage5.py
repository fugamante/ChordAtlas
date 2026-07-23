from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

from chordatlas.acquisition import DirectHttpsSource, DownloadResult
from chordatlas.studio import StudioServer, create_server
from test_studio_stage2 import bootstrap, progression_wav, request
from test_studio_stage3 import succeeded_run
from test_studio_stage4 import _mapping, _promotion_post, _ready_review


class FixtureTransport:
    """Authorized synthetic transport; it never performs a network request."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def download(
        self,
        source: DirectHttpsSource,
        stage_path: Path,
        *,
        progress,
        cancelled,
        max_bytes: int,
    ) -> DownloadResult:
        self.calls.append(source.url)
        assert len(self.payload) <= max_bytes
        if cancelled():
            raise AssertionError("fixture was cancelled before download")
        stage_path.write_bytes(self.payload)
        progress(len(self.payload), len(self.payload))
        return DownloadResult(len(self.payload), source.url)


@contextmanager
def acquisition_server(
    tmp_path: Path,
    transport: FixtureTransport,
) -> Iterator[StudioServer]:
    server = create_server(
        tmp_path,
        port=0,
        max_upload_bytes=2 * 1024 * 1024,
        acquisition_transport=transport,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(server, cookie: str, path: str, payload: dict, *, key: str):
    body = json.dumps(payload).encode()
    return request(
        server,
        "POST",
        path,
        headers={
            "Cookie": cookie,
            "Origin": server.origin,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Idempotency-Key": key,
        },
        body=body,
    )


def test_studio_authorized_acquisition_reuses_safe_waveform_source(
    tmp_path: Path,
) -> None:
    transport = FixtureTransport(progression_wav())
    secret = "https://media.example.test/private/take?signature=never-public"
    with acquisition_server(tmp_path, transport) as server:
        cookie = bootstrap(server)
        status, _headers, body = _post(
            server,
            cookie,
            "/api/acquisitions",
            {
                "url": secret,
                "display_name": "Authorized rehearsal take",
                "authorization_confirmed": True,
            },
            key="studio-stage5-acquire",
        )
        assert status == HTTPStatus.ACCEPTED, body
        acquisition = json.loads(body)["acquisition"]
        run_id = acquisition["run_id"]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, _headers, body = request(
                server,
                "GET",
                f"/api/acquisitions/{run_id}",
                headers={"Cookie": cookie},
            )
            assert status == HTTPStatus.OK, body
            acquisition = json.loads(body)["acquisition"]
            if acquisition["status"] not in {"queued", "running", "cancel_requested"}:
                break
            time.sleep(0.02)
        assert acquisition["status"] == "succeeded", acquisition
        assert acquisition["result"]["source"]["display_name"] == "Authorized rehearsal take"

        source_id = acquisition["source_id"]
        status, _headers, source_body = request(
            server,
            "GET",
            "/api/sources",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        sources = json.loads(source_body)["sources"]
        assert [item["source"]["id"] for item in sources] == [source_id]
        assert sources[0]["source_kind"] == "direct_https_wav"

        status, _headers, waveform_body = request(
            server,
            "GET",
            f"/api/sources/{source_id}/waveform",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        assert json.loads(waveform_body)["timebase"]["unit"] == "sample_frame"

        public = body + source_body + waveform_body
        assert secret.encode() not in public
        assert b"never-public" not in public
        assert b"media.example.test" not in public
        assert transport.calls == [secret]

        status, _headers, forget_body = _post(
            server,
            cookie,
            f"/api/acquisitions/{run_id}/forget",
            {},
            key="unused-by-forget",
        )
        assert status == HTTPStatus.OK, forget_body
        forgotten = json.loads(forget_body)["acquisition"]
        assert forgotten["locator_retained"] is False
        assert forgotten["status"] == "succeeded"
        assert not (server.acquisition.store.private_root / f"{run_id}.json").exists()
        assert not (server.store.locators_root / f"{source_id}.json").exists()
        assert server.store.source(source_id).display_name == "Authorized rehearsal take"


def test_studio_acquisition_requires_authorization_before_transport(
    tmp_path: Path,
) -> None:
    transport = FixtureTransport(progression_wav())
    with acquisition_server(tmp_path, transport) as server:
        cookie = bootstrap(server)
        status, _headers, body = _post(
            server,
            cookie,
            "/api/acquisitions",
            {
                "url": "https://media.example.test/take",
                "display_name": "Take",
                "authorization_confirmed": False,
            },
            key="studio-stage5-no-authorization",
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert json.loads(body)["error"]["code"] == "authorization_required"
        assert transport.calls == []


def test_stage5_ui_separates_private_url_authorization_and_job_actions(
    tmp_path: Path,
) -> None:
    transport = FixtureTransport(progression_wav())
    with acquisition_server(tmp_path, transport) as server:
        status, _headers, body = request(server, "GET", "/index.html")
        assert status == HTTPStatus.OK
        html = body.decode()
        for identifier in (
            'id="remote-name"',
            'id="remote-url" type="password"',
            'id="toggle-remote-url"',
            'id="remote-authorization"',
            'id="acquire"',
            'id="acquisition-progress"',
            'id="acquisition-status"',
            'id="cancel-acquisition"',
            'id="retry-acquisition"',
            'id="forget-acquisition"',
            'aria-label="Remote acquisition progress"',
        ):
            assert identifier in html
        assert "webpage that plays or describes audio is not a" in html
        assert "direct media URL" in html


def test_direct_media_reaches_reviewed_songchart_exports_without_locator(
    tmp_path: Path,
) -> None:
    transport = FixtureTransport(progression_wav())
    secret = "https://media.example.test/take?credential=stage5-private"
    with acquisition_server(tmp_path, transport) as server:
        cookie = bootstrap(server)
        status, _headers, body = _post(
            server,
            cookie,
            "/api/acquisitions",
            {
                "url": secret,
                "display_name": "Stage 5 synthetic take",
                "authorization_confirmed": True,
            },
            key="stage5-e2e-acquisition",
        )
        assert status == HTTPStatus.ACCEPTED, body
        acquisition = json.loads(body)["acquisition"]
        for _ in range(250):
            status, _headers, body = request(
                server,
                "GET",
                f"/api/acquisitions/{acquisition['run_id']}",
                headers={"Cookie": cookie},
            )
            assert status == HTTPStatus.OK, body
            acquisition = json.loads(body)["acquisition"]
            if acquisition["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert acquisition["status"] == "succeeded"

        run_id = succeeded_run(server, cookie, acquisition["source_id"])
        review = _ready_review(server, cookie, run_id)
        mapping = _mapping(review)
        preview_payload = {
            "revision_id": review["head"]["revision_id"],
            "mapping": mapping,
        }
        status, _headers, body = _promotion_post(
            server,
            cookie,
            f"/api/review-sessions/{review['session']['session_id']}/promotion-preview",
            preview_payload,
            **{"If-Match": '"{}"'.format(review["head"]["token"])},
        )
        assert status == HTTPStatus.OK, body
        preview = json.loads(body)["preview"]
        material = sorted(
            item["id"] for item in preview["issues"] if item["severity"] == "material"
        )
        status, _headers, body = _promotion_post(
            server,
            cookie,
            f"/api/review-sessions/{review['session']['session_id']}/approvals",
            {
                **preview_payload,
                "expected_spec_id": preview["spec_id"],
                "expected_result_id": preview["result_id"],
                "expected_issue_digest": preview["issue_digest"],
                "acknowledged_issue_ids": material,
            },
            **{
                "If-Match": '"{}"'.format(review["head"]["token"]),
                "Idempotency-Key": "stage5-e2e-approval",
            },
        )
        assert status == HTTPStatus.CREATED, body
        approval = json.loads(body)["approval"]
        status, _headers, body = request(
            server,
            "GET",
            f"/api/approvals/{approval['approval_id']}/exports",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        exports = json.loads(body)["exports"]
        assert json.loads(exports["json"])["schema_version"] == "1.0.0"
        for rendered in exports.values():
            assert secret not in rendered
            assert "stage5-private" not in rendered
            assert "media.example.test" not in rendered
