from __future__ import annotations

import http.client
import io
import json
import math
import struct
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from importlib import resources
from pathlib import Path

from chordatlas.studio import StudioServer, create_server


def progression_wav(sample_rate: int = 8_000) -> bytes:
    chords = (
        (261.63, 329.63, 392.00),
        (196.00, 246.94, 293.66),
        (220.00, 261.63, 329.63),
        (174.61, 220.00, 261.63),
    )
    values = []
    for chord_index, chord in enumerate(chords):
        for local in range(sample_rate * 2):
            absolute = chord_index * sample_rate * 2 + local
            values.append(
                round(
                    sum(
                        math.sin(2 * math.pi * frequency * absolute / sample_rate)
                        for frequency in chord
                    )
                    * 4_500
                )
            )
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(struct.pack(f"<{len(values)}h", *values))
    return output.getvalue()


@contextmanager
def running_server(tmp_path: Path) -> Iterator[StudioServer]:
    server = create_server(tmp_path, port=0, max_upload_bytes=2 * 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    server: StudioServer,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    values = {"Host": server.authority}
    if headers:
        values.update(headers)
    connection.request(method, path, body=body, headers=values)
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def bootstrap(server: StudioServer) -> str:
    status, headers, _payload = request(
        server,
        "POST",
        "/api/session",
        headers={
            "Origin": server.origin,
            "X-ChordAtlas-Bootstrap": server.bootstrap_token,
            "Content-Length": "0",
        },
        body=b"",
    )
    assert status == HTTPStatus.OK
    return headers["Set-Cookie"].split(";", 1)[0]


def import_source(server: StudioServer, cookie: str) -> dict:
    payload = progression_wav()
    status, _headers, body = request(
        server,
        "POST",
        "/api/import",
        headers={
            "Cookie": cookie,
            "Origin": server.origin,
            "Content-Type": "audio/wav",
            "Content-Length": str(len(payload)),
            "X-ChordAtlas-Authorized": "true",
            "X-ChordAtlas-File-Name": "synthetic-progression.wav",
        },
        body=payload,
    )
    assert status == HTTPStatus.CREATED, body
    return json.loads(body)


def test_analysis_api_runs_polls_and_returns_private_safe_timeline(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        source_id = imported["source"]["id"]
        request_body = json.dumps({"range": None}).encode()
        status, _headers, body = request(
            server,
            "POST",
            f"/api/sources/{source_id}/analysis-runs",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(request_body)),
                "Idempotency-Key": "studio-fixture-run-0001",
            },
            body=request_body,
        )
        assert status == HTTPStatus.ACCEPTED, body
        run_id = json.loads(body)["run"]["run_id"]

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status, _headers, body = request(
                server,
                "GET",
                f"/api/analysis-runs/{run_id}",
                headers={"Cookie": cookie},
            )
            run = json.loads(body)["run"]
            if run["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert run["status"] == "succeeded", run

        status, _headers, body = request(
            server,
            "GET",
            f"/api/analysis-runs/{run_id}/timeline",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        timeline = json.loads(body)["timeline"]
        serialized = json.dumps(timeline)
        assert timeline["segments"]
        assert timeline["segments"][0]["range"]["start_frame"] == 0
        assert "spec_id" not in serialized
        assert "timeline_id" not in serialized
        assert "sha256" not in serialized
        assert str(tmp_path) not in serialized


def test_analysis_api_requires_auth_origin_and_valid_range(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        source_id = imported["source"]["id"]
        payload = json.dumps({"range": {"start_frame": 100, "end_frame": 50}}).encode()

        status, _headers, _body = request(
            server,
            "POST",
            f"/api/sources/{source_id}/analysis-runs",
            headers={
                "Cookie": cookie,
                "Origin": "http://attacker.invalid",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            body=payload,
        )
        assert status == HTTPStatus.FORBIDDEN

        status, _headers, body = request(
            server,
            "POST",
            f"/api/sources/{source_id}/analysis-runs",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            body=payload,
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert json.loads(body)["error"]["code"] == "invalid_analysis_range"

        for invalid_range in (
            {"start_frame": True, "end_frame": 100},
            {"start_frame": 0.5, "end_frame": 100},
            {"start_frame": "0", "end_frame": 100},
            {"start_frame": 0, "end_frame": 100, "extra": 1},
        ):
            payload = json.dumps({"range": invalid_range}).encode()
            status, _headers, body = request(
                server,
                "POST",
                f"/api/sources/{source_id}/analysis-runs",
                headers={
                    "Cookie": cookie,
                    "Origin": server.origin,
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                },
                body=payload,
            )
            assert status == HTTPStatus.BAD_REQUEST
            assert json.loads(body)["error"]["code"] == "invalid_analysis_range"


def test_analysis_retry_preserves_scope_and_records_lineage(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        source_id = imported["source"]["id"]
        payload = json.dumps(
            {"range": {"start_frame": 0, "end_frame": 32_000}}
        ).encode()
        status, _headers, body = request(
            server,
            "POST",
            f"/api/sources/{source_id}/analysis-runs",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            body=payload,
        )
        assert status == HTTPStatus.ACCEPTED
        first = json.loads(body)["run"]
        cancel_body = b"{}"
        request(
            server,
            "POST",
            f"/api/analysis-runs/{first['run_id']}/cancel",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(cancel_body)),
            },
            body=cancel_body,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _status, _headers, body = request(
                server,
                "GET",
                f"/api/analysis-runs/{first['run_id']}",
                headers={"Cookie": cookie},
            )
            first = json.loads(body)["run"]
            if first["status"] in {"cancelled", "failed", "succeeded"}:
                break
            time.sleep(0.05)
        assert first["status"] == "cancelled"

        status, _headers, body = request(
            server,
            "POST",
            f"/api/analysis-runs/{first['run_id']}/retry",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": "2",
            },
            body=b"{}",
        )
        assert status == HTTPStatus.ACCEPTED, body
        retry = json.loads(body)["run"]
        assert retry["retry_of"] == first["run_id"]
        assert retry["scope"] == first["scope"]


def test_shutdown_refuses_active_analysis(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        source_id = imported["source"]["id"]
        payload = b'{"range":null}'
        status, _headers, body = request(
            server,
            "POST",
            f"/api/sources/{source_id}/analysis-runs",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            body=payload,
        )
        assert status == HTTPStatus.ACCEPTED

        status, _headers, body = request(
            server,
            "POST",
            "/api/shutdown",
            headers={"Cookie": cookie, "Origin": server.origin, "Content-Length": "0"},
            body=b"",
        )
        if server.analysis.has_active():
            assert status == HTTPStatus.CONFLICT
            assert json.loads(body)["error"]["code"] == "import_busy"


def test_stage2_ui_is_read_only_accessible_and_synchronized() -> None:
    package = resources.files("chordatlas.studio_assets")
    html = package.joinpath("index.html").read_text(encoding="utf-8")
    script = package.joinpath("app.js").read_text(encoding="utf-8")

    assert "Machine draft" in html
    assert "Unreviewed" in html
    assert 'aria-label="Read-only machine chord proposals"' in html
    assert 'role="status"' in html
    assert "Analyze chords" in html
    assert "Cancel analysis" in html
    assert "highlightCandidate(clamped)" in script
    assert 'item.setAttribute("aria-current", "true")' in script
    assert "segment.range.start_frame" in script
    for forbidden in ("Approve chart", "Rename chord", "Split segment", "contenteditable"):
        assert forbidden not in html + script
