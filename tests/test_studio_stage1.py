from __future__ import annotations

import http.client
import io
import json
import math
import os
import struct
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

from chordatlas.studio import StudioServer, create_server


def synthetic_wav(*, frames: int = 256, sample_rate: int = 8_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        samples = [round(math.sin(frame / 8) * 12_000) for frame in range(frames)]
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


@contextmanager
def running_server(tmp_path: Path) -> Iterator[StudioServer]:
    server = create_server(tmp_path, port=0, max_upload_bytes=1024 * 1024)
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
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    request_headers = {"Host": server.authority}
    if headers:
        request_headers.update(headers)
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    return response.status, response_headers, payload


def bootstrap(server: StudioServer) -> str:
    status, headers, payload = request(
        server,
        "POST",
        "/api/session",
        headers={
            "Origin": server.origin,
            "Sec-Fetch-Site": "same-origin",
            "X-ChordAtlas-Bootstrap": server.bootstrap_token,
            "Content-Length": "0",
        },
        body=b"",
    )
    assert status == HTTPStatus.OK, payload
    return headers["Set-Cookie"].split(";", 1)[0]


def test_static_ui_is_local_packaged_and_hardened(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        status, headers, payload = request(server, "GET", "/")

    assert status == HTTPStatus.OK
    assert b"ChordAtlas Studio" in payload
    assert b"authorization" in payload
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


def test_api_requires_single_use_bootstrap_and_session(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        status, _headers, _payload = request(server, "GET", "/api/sources")
        assert status == HTTPStatus.FORBIDDEN

        cookie = bootstrap(server)
        status, _headers, _payload = request(
            server,
            "POST",
            "/api/session",
            headers={
                "Origin": server.origin,
                "Sec-Fetch-Site": "same-origin",
                "X-ChordAtlas-Bootstrap": server.bootstrap_token,
                "Content-Length": "0",
            },
            body=b"",
        )
        assert status == HTTPStatus.FORBIDDEN

        status, _headers, payload = request(
            server,
            "GET",
            "/api/sources",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        assert json.loads(payload) == {"sources": []}


def test_concurrent_studios_use_distinct_cookie_names(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = create_server(first_root, port=0)
    second = create_server(second_root, port=0)
    try:
        assert first.cookie_name != second.cookie_name
    finally:
        first.server_close()
        second.server_close()


def test_shutdown_rejects_active_import(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        assert server.begin_import()
        try:
            status, _headers, body = request(
                server,
                "POST",
                "/api/shutdown",
                headers={
                    "Cookie": cookie,
                    "Origin": server.origin,
                    "Content-Length": "0",
                },
                body=b"",
            )
            assert status == HTTPStatus.CONFLICT
            assert json.loads(body)["error"]["code"] == "import_busy"
        finally:
            server.finish_import()


def test_host_origin_and_cross_site_requests_fail_closed(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        status, _headers, _payload = request(
            server,
            "GET",
            "/",
            headers={"Host": f"attacker.invalid:{server.server_port}"},
        )
        assert status == HTTPStatus.FORBIDDEN

        status, _headers, _payload = request(
            server,
            "POST",
            "/api/session",
            headers={
                "Origin": "null",
                "X-ChordAtlas-Bootstrap": server.bootstrap_token,
                "Content-Length": "0",
            },
            body=b"",
        )
        assert status == HTTPStatus.FORBIDDEN

        status, _headers, _payload = request(
            server,
            "GET",
            "/",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert status == HTTPStatus.FORBIDDEN


def test_import_waveform_and_authenticated_single_range_playback(tmp_path: Path) -> None:
    payload = synthetic_wav()
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        status, _headers, body = request(
            server,
            "POST",
            "/api/import",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "audio/wav",
                "Content-Length": str(len(payload)),
                "X-ChordAtlas-Authorized": "true",
                "X-ChordAtlas-File-Name": "synthetic.wav",
            },
            body=payload,
        )
        assert status == HTTPStatus.CREATED, body
        imported = json.loads(body)
        serialized = json.dumps(imported)
        assert str(tmp_path) not in serialized
        assert "sha256" not in serialized
        assert "RIFF" not in serialized

        status, _headers, body = request(
            server,
            "GET",
            imported["waveform_url"],
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        waveform = json.loads(body)
        assert waveform["timebase"]["duration_frames"] == 256

        status, headers, body = request(
            server,
            "GET",
            imported["playback_url"],
            headers={"Cookie": cookie, "Range": "bytes=4-15"},
        )
        assert status == HTTPStatus.PARTIAL_CONTENT
        assert headers["Content-Range"] == f"bytes 4-15/{len(payload)}"
        assert body == payload[4:16]

        status, _headers, _body = request(
            server,
            "GET",
            imported["playback_url"],
            headers={"Range": "bytes=0-1"},
        )
        assert status == HTTPStatus.FORBIDDEN

        status, headers, body = request(
            server,
            "GET",
            imported["playback_url"],
            headers={"Cookie": cookie, "Range": "bytes=0-1,4-5"},
        )
        assert status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
        assert headers["Content-Range"] == f"bytes */{len(payload)}"
        assert body == b""


def test_playback_stream_uses_one_verified_descriptor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = synthetic_wav()
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        status, _headers, body = request(
            server,
            "POST",
            "/api/import",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "audio/wav",
                "Content-Length": str(len(payload)),
                "X-ChordAtlas-Authorized": "true",
                "X-ChordAtlas-File-Name": "synthetic.wav",
            },
            body=payload,
        )
        assert status == HTTPStatus.CREATED
        imported = json.loads(body)
        original_open = server.store.open_audio_for_source

        def open_then_retarget(source_id, **kwargs):
            asset, handle = original_open(source_id, **kwargs)
            blob = server.store.audio_path_for_source(source_id)
            displaced = blob.with_suffix(".verified")
            blob.rename(displaced)
            replacement = tmp_path / "replacement.wav"
            replacement.write_bytes(b"X" * len(payload))
            os.chmod(replacement, 0o600)
            os.replace(replacement, blob)
            return asset, handle

        monkeypatch.setattr(server.store, "open_audio_for_source", open_then_retarget)
        status, headers, body = request(
            server,
            "GET",
            imported["playback_url"],
            headers={"Cookie": cookie, "Range": "bytes=0-31"},
        )

        assert status == HTTPStatus.PARTIAL_CONTENT
        assert headers["Content-Range"] == f"bytes 0-31/{len(payload)}"
        assert body == payload[:32]


def test_import_requires_authorization_and_exact_origin(tmp_path: Path) -> None:
    payload = synthetic_wav()
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        common = {
            "Cookie": cookie,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "audio/wav",
            "Content-Length": str(len(payload)),
            "X-ChordAtlas-File-Name": "synthetic.wav",
        }
        status, _headers, body = request(
            server,
            "POST",
            "/api/import",
            headers={**common, "Origin": "http://attacker.invalid"},
            body=payload,
        )
        assert status == HTTPStatus.FORBIDDEN

        status, _headers, body = request(
            server,
            "POST",
            "/api/import",
            headers={**common, "Origin": server.origin},
            body=payload,
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert json.loads(body)["error"]["code"] == "authorization_required"
        assert server.store.list_public_sources() == []


def test_suffix_open_and_unsatisfiable_ranges(tmp_path: Path) -> None:
    payload = synthetic_wav()
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
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
                "X-ChordAtlas-File-Name": "synthetic.wav",
            },
            body=payload,
        )
        playback_url = json.loads(body)["playback_url"]

        status, _headers, body = request(
            server,
            "GET",
            playback_url,
            headers={"Cookie": cookie, "Range": "bytes=-8"},
        )
        assert status == HTTPStatus.PARTIAL_CONTENT
        assert body == payload[-8:]

        status, _headers, body = request(
            server,
            "GET",
            playback_url,
            headers={"Cookie": cookie, "Range": "bytes=8-"},
        )
        assert status == HTTPStatus.PARTIAL_CONTENT
        assert body == payload[8:]

        status, _headers, _body = request(
            server,
            "GET",
            playback_url,
            headers={"Cookie": cookie, "Range": f"bytes={len(payload)}-"},
        )
        assert status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE


def test_ui_assets_contain_frame_clock_and_accessible_controls() -> None:
    from importlib import resources

    package = resources.files("chordatlas.studio_assets")
    html = package.joinpath("index.html").read_text(encoding="utf-8")
    script = package.joinpath("app.js").read_text(encoding="utf-8")

    assert 'id="authorization"' in html
    assert 'id="waveform"' in html
    assert 'id="waveform" tabindex=' not in html
    assert 'aria-live="polite"' in html
    assert 'id="loop-start"' in html
    assert "Math.round(audio.currentTime * sampleRate)" in script
    assert "loopRange.start" in script
    assert 'audio.addEventListener("timeupdate"' in script
    assert 'audio.addEventListener("ended", restartLoopAtMediaEnd)' in script
    assert 'await loadSources();' in script
    assert 'fileInput.addEventListener("change", resetFileAuthorization)' in script
    assert "authorization.checked = false;" in script
