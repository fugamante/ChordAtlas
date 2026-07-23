from __future__ import annotations

import io
import importlib
import json
import math
import os
import socket
import ssl
import stat
import struct
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from chordatlas.acquisition import (
    AcquisitionError,
    AcquisitionService,
    DirectHttpsSource,
    DownloadResult,
    PinnedHttpsTransport,
)
from chordatlas.acquisition.store import AcquisitionStore
from chordatlas.media import ProjectMediaStore, RemoteLocator


def synthetic_wav(*, frames: int = 512, sample_rate: int = 8_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        samples = [round(math.sin(frame / 16) * 12_000) for frame in range(frames)]
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class FakeTransport:
    def __init__(self, payload: bytes | None = None, error: AcquisitionError | None = None):
        self.payload = payload or synthetic_wav()
        self.error = error
        self.calls: list[str] = []

    def download(self, source, stage_path, *, progress, cancelled, max_bytes):
        self.calls.append(source.url)
        if self.error:
            raise self.error
        if cancelled():
            raise AcquisitionError("acquisition_cancelled", "Acquisition cancelled.")
        assert len(self.payload) <= max_bytes
        stage_path.write_bytes(self.payload)
        os.chmod(stage_path, 0o600)
        progress(len(self.payload), len(self.payload))
        return DownloadResult(len(self.payload), source.url)


class BlockingTransport(FakeTransport):
    def download(self, source, stage_path, *, progress, cancelled, max_bytes):
        self.calls.append(source.url)
        while not cancelled():
            threading.Event().wait(0.01)
        raise AcquisitionError("acquisition_cancelled", "Acquisition cancelled.")


class _RawSocket:
    def close(self) -> None:
        return


class _TlsSocket:
    def __init__(self, response: bytes, peer: str) -> None:
        self.response = response
        self.peer = peer
        self.sent = b""
        self.closed = False

    def getpeername(self):
        return (self.peer, 443)

    def settimeout(self, _value) -> None:
        return

    def sendall(self, value: bytes) -> None:
        self.sent += value

    def makefile(self, _mode: str):
        return io.BytesIO(self.response)

    def close(self) -> None:
        self.closed = True


class _TlsContext:
    check_hostname = True
    verify_mode = ssl.CERT_REQUIRED
    minimum_version = ssl.TLSVersion.TLSv1_2

    def __init__(self, sockets: list[_TlsSocket]) -> None:
        self.sockets = sockets
        self.hostnames: list[str] = []
        self.alpn: list[str] = []

    def set_alpn_protocols(self, protocols: list[str]) -> None:
        self.alpn = protocols

    def wrap_socket(self, _raw, *, server_hostname: str):
        self.hostnames.append(server_hostname)
        return self.sockets.pop(0)


def _response(
    status: str = "200 OK",
    *,
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
) -> bytes:
    lines = [f"HTTP/1.1 {status}", *(f"{name}: {value}" for name, value in headers), "", ""]
    return "\r\n".join(lines).encode("ascii") + body


def _pinned_transport(responses: list[bytes], *, peer: str = "93.184.216.34"):
    sockets = [_TlsSocket(response, peer) for response in responses]
    context = _TlsContext(sockets.copy())
    connected: list[tuple[str, int]] = []

    def connect(target, **_kwargs):
        connected.append(target)
        return _RawSocket()

    resolver = lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (peer, 443)),
    ]
    transport = PinnedHttpsTransport(
        resolver=resolver,
        socket_factory=connect,
        ssl_context=context,
        timeout_seconds=2,
    )
    return transport, context, connected, sockets


def test_authorization_is_durable_gate_before_transport(tmp_path: Path) -> None:
    transport = FakeTransport()
    service = AcquisitionService(tmp_path, transport=transport)

    with pytest.raises(AcquisitionError) as denied:
        service.start(
            url="https://media.example.test/take",
            display_name="take.wav",
            authorization_confirmed=False,
        )

    assert denied.value.code == "authorization_required"
    assert transport.calls == []
    assert list(service.store.requests_root.iterdir()) == []


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://audio.example/take", "invalid_source"),
        ("https://user:pass@audio.example/take", "invalid_source"),
        ("https://127.0.0.1/take", "ip_literal_forbidden"),
        ("https://youtube.com/watch?v=x", "hosted_page_unsupported"),
        ("https://audio.example:444/take", "invalid_source"),
        ("https://audio.example/take#fragment", "invalid_source"),
        ("https://audio.example/with space", "invalid_source"),
        ("https://audio.example\\@private.test/take", "invalid_source"),
        ("https://localhost/take", "hosted_page_unsupported"),
        ("https://printer.local/take", "hosted_page_unsupported"),
        ("https://áudio.example/take", "invalid_source"),
    ],
)
def test_source_classification_rejects_unsafe_or_hosted_inputs(url: str, code: str) -> None:
    with pytest.raises(AcquisitionError) as rejected:
        DirectHttpsSource.classify(url)
    assert rejected.value.code == code


def test_suffix_is_not_used_as_media_proof() -> None:
    source = DirectHttpsSource.classify("https://audio.example/download?id=take")
    assert source.target == "/download?id=take"


def test_dns_rejects_private_and_mixed_answers() -> None:
    private = lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("127.0.0.1", 443)),
    ]
    mixed = lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("10.0.0.1", 443)),
    ]

    with pytest.raises(AcquisitionError, match="non-public"):
        PinnedHttpsTransport(resolver=private)._safe_addresses("audio.example")
    with pytest.raises(AcquisitionError, match="non-public"):
        PinnedHttpsTransport(resolver=mixed)._safe_addresses("audio.example")


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:93.184.216.34",
        "2002:5db8:d822::1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "64:ff9b::5db8:d822",
    ],
)
def test_dns_rejects_mapped_and_transition_addresses(address: str) -> None:
    resolver = lambda *_args, **_kwargs: [
        (10, 1, 6, "", (address, 443, 0, 0)),
    ]
    with pytest.raises(AcquisitionError, match="non-public"):
        PinnedHttpsTransport(resolver=resolver)._safe_addresses("audio.example")


@pytest.mark.parametrize("address", ["192.0.0.9", "192.88.99.1"])
def test_dns_rejects_special_purpose_ipv4_even_when_runtime_marks_global(
    address: str,
) -> None:
    resolver = lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)),
    ]
    with pytest.raises(AcquisitionError, match="non-public"):
        PinnedHttpsTransport(resolver=resolver)._safe_addresses("audio.example")


def test_transport_pins_numeric_peer_while_preserving_sni_and_host(tmp_path: Path) -> None:
    payload = synthetic_wav()
    raw = _response(
        headers=(
            ("Content-Length", str(len(payload))),
            ("Content-Type", "audio/wav"),
        ),
        body=payload,
    )
    transport, context, connected, sockets = _pinned_transport([raw])
    stage = ProjectMediaStore.initialize(tmp_path).allocate_private_stage()
    source = DirectHttpsSource.classify(
        "https://audio.example/download?private=sent-only-to-origin"
    )
    result = transport.download(
        source,
        stage,
        progress=lambda *_args: None,
        cancelled=lambda: False,
        max_bytes=len(payload),
    )

    assert result.byte_length == len(payload)
    assert connected == [("93.184.216.34", 443)]
    assert context.hostnames == ["audio.example"]
    assert context.alpn == ["http/1.1"]
    assert b"Host: audio.example\r\n" in sockets[0].sent
    assert b"GET /download?private=sent-only-to-origin HTTP/1.1\r\n" in sockets[0].sent
    assert stage.read_bytes() == payload


def test_transport_rejects_peer_mismatch_before_http() -> None:
    transport, _context, _connected, _sockets = _pinned_transport(
        [_response()],
        peer="93.184.216.35",
    )
    source = DirectHttpsSource.classify("https://audio.example/take")
    with pytest.raises(AcquisitionError, match="validated address"):
        transport._request(source, "93.184.216.34", timeout=1)


def test_transport_manual_same_origin_redirect_revalidates_each_hop(
    tmp_path: Path,
) -> None:
    payload = synthetic_wav()
    redirect = _response(
        "302 Found",
        headers=(("Location", "/final?opaque=1"),),
    )
    final = _response(
        headers=(
            ("Content-Length", str(len(payload))),
            ("Content-Type", "application/octet-stream"),
        ),
        body=payload,
    )
    transport, context, connected, _sockets = _pinned_transport([redirect, final])
    stage = ProjectMediaStore.initialize(tmp_path).allocate_private_stage()
    result = transport.download(
        DirectHttpsSource.classify("https://audio.example/start"),
        stage,
        progress=lambda *_args: None,
        cancelled=lambda: False,
        max_bytes=len(payload),
    )

    assert result.final_url == "https://audio.example/final?opaque=1"
    assert connected == [("93.184.216.34", 443), ("93.184.216.34", 443)]
    assert context.hostnames == ["audio.example", "audio.example"]


def test_transport_rejects_cross_origin_redirect_before_second_resolution(
    tmp_path: Path,
) -> None:
    redirect = _response(
        "302 Found",
        headers=(("Location", "https://other.example/final"),),
    )
    transport, _context, connected, _sockets = _pinned_transport([redirect])
    stage = ProjectMediaStore.initialize(tmp_path).allocate_private_stage()
    with pytest.raises(AcquisitionError) as rejected:
        transport.download(
            DirectHttpsSource.classify("https://audio.example/start"),
            stage,
            progress=lambda *_args: None,
            cancelled=lambda: False,
            max_bytes=1024,
        )
    assert rejected.value.code == "cross_origin_redirect"
    assert connected == [("93.184.216.34", 443)]


@pytest.mark.parametrize(
    ("headers", "body", "code"),
    [
        (
            (("Content-Length", "4"), ("Content-Length", "4"), ("Content-Type", "audio/wav")),
            b"1234",
            "content_length_required",
        ),
        (
            (
                ("Content-Length", "4"),
                ("Transfer-Encoding", "chunked"),
                ("Content-Type", "audio/wav"),
            ),
            b"1234",
            "ambiguous_framing",
        ),
        (
            (
                ("Content-Length", "4"),
                ("Content-Type", "audio/wav"),
                ("Content-Encoding", "gzip"),
            ),
            b"1234",
            "encoded_response",
        ),
        (
            (("Content-Length", "4"), ("Content-Type", "text/html")),
            b"html",
            "unsupported_content_type",
        ),
        (
            (("Content-Length", "8"), ("Content-Type", "audio/wav")),
            b"short",
            "download_truncated",
        ),
    ],
)
def test_transport_rejects_ambiguous_or_invalid_response_framing(
    tmp_path: Path,
    headers,
    body,
    code: str,
) -> None:
    transport, _context, _connected, _sockets = _pinned_transport(
        [_response(headers=headers, body=body)]
    )
    stage = ProjectMediaStore.initialize(tmp_path).allocate_private_stage()
    with pytest.raises(AcquisitionError) as rejected:
        transport.download(
            DirectHttpsSource.classify("https://audio.example/take"),
            stage,
            progress=lambda *_args: None,
            cancelled=lambda: False,
            max_bytes=1024,
        )
    assert rejected.value.code == code


def test_declared_oversize_is_actionable_and_not_retryable(tmp_path: Path) -> None:
    transport, _context, _connected, _sockets = _pinned_transport(
        [
            _response(
                headers=(
                    ("Content-Length", "1025"),
                    ("Content-Type", "audio/wav"),
                )
            )
        ]
    )
    stage = ProjectMediaStore.initialize(tmp_path).allocate_private_stage()
    with pytest.raises(AcquisitionError) as rejected:
        transport.download(
            DirectHttpsSource.classify("https://audio.example/take"),
            stage,
            progress=lambda *_args: None,
            cancelled=lambda: False,
            max_bytes=1024,
        )
    assert rejected.value.code == "download_too_large"
    assert not rejected.value.retryable
    assert "Local WAV" in rejected.value.public_message


def test_fake_transport_full_flow_publishes_private_remote_locator(tmp_path: Path) -> None:
    transport = FakeTransport()
    service = AcquisitionService(tmp_path, transport=transport)
    run_id = service.start(
        url="https://audio.example/download?token=private",
        display_name="/Users/person/session/take.wav",
        authorization_confirmed=True,
        idempotency_key="request-0001",
    )
    state = service.wait(run_id, 5)

    assert state["status"] == "succeeded"
    assert state["phase"] == "complete"
    source = service.media.source(state["source_id"])
    assert source.display_name == "take.wav"
    assert service.media.asset_for_source(source.id).timebase.duration_frames == 512
    locator = service.media.locators_root / f"{source.id}.json"
    assert stat.S_IMODE(locator.stat().st_mode) == 0o600
    assert json.loads(locator.read_text())["channel"] == "direct_https"
    assert "token=private" in locator.read_text()


def test_public_media_and_acquisition_status_exclude_url_and_path(tmp_path: Path) -> None:
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    secret = "private-signed-query"
    run_id = service.start(
        url=f"https://audio.example/media?token={secret}",
        display_name="/private/person/take.wav",
        authorization_confirmed=True,
    )
    state = service.wait(run_id, 5)
    public = json.dumps(
        {"status": service.status(run_id), "sources": service.media.list_public_sources()}
    )

    assert state["status"] == "succeeded"
    assert secret not in public
    assert "audio.example" not in public
    assert "/private/person" not in public


def test_idempotency_survives_service_restart(tmp_path: Path) -> None:
    first = AcquisitionService(tmp_path, transport=FakeTransport())
    args = {
        "url": "https://audio.example/take",
        "display_name": "take.wav",
        "authorization_confirmed": True,
        "idempotency_key": "same-request",
    }
    run_id = first.start(**args)
    assert first.wait(run_id, 5)["status"] == "succeeded"

    second_transport = FakeTransport()
    second = AcquisitionService(tmp_path, transport=second_transport)
    assert second.start(**args) == run_id
    assert second_transport.calls == []


def test_concurrent_services_converge_on_one_idempotent_acquisition(
    tmp_path: Path,
) -> None:
    first_transport = FakeTransport()
    second_transport = FakeTransport()
    first = AcquisitionService(tmp_path, transport=first_transport)
    second = AcquisitionService(tmp_path, transport=second_transport)
    barrier = threading.Barrier(2)

    def submit(service: AcquisitionService) -> str:
        barrier.wait()
        return service.start(
            url="https://audio.example/take",
            display_name="take.wav",
            authorization_confirmed=True,
            idempotency_key="concurrent-request",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(submit, (first, second)))

    assert ids[0] == ids[1]
    for _ in range(200):
        state = first.status(ids[0])
        if state["status"] == "succeeded":
            break
        threading.Event().wait(0.01)
    assert state["status"] == "succeeded"
    assert len(ProjectMediaStore.open(tmp_path).list_public_sources()) == 1
    assert len(first_transport.calls) + len(second_transport.calls) == 1


def test_failure_is_retryable_and_retry_has_new_source(tmp_path: Path) -> None:
    failed_transport = FakeTransport(
        error=AcquisitionError("network_failed", "Download failed safely.")
    )
    service = AcquisitionService(tmp_path, transport=failed_transport)
    first = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    first_state = service.wait(first, 5)
    assert first_state["status"] == "failed"
    assert first_state["failure_code"] == "network_failed"

    service.transport = FakeTransport()
    second = service.retry(first)
    second_state = service.wait(second, 5)
    assert second_state["status"] == "succeeded"
    assert second_state["retry_of"] == first
    assert second_state["source_id"] != first_state["source_id"]

    service.forget_locator(second)
    assert not (service.store.private_root / f"{first}.json").exists()
    assert not (service.store.private_root / f"{second}.json").exists()
    assert not (
        service.media.locators_root / f"{second_state['source_id']}.json"
    ).exists()


def test_cancel_is_terminal_and_service_reports_no_active_job(tmp_path: Path) -> None:
    service = AcquisitionService(tmp_path, transport=BlockingTransport())
    run_id = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    for _ in range(100):
        if service.status(run_id)["status"] == "running":
            break
        threading.Event().wait(0.01)
    service.cancel(run_id)
    state = service.wait(run_id, 5)

    assert state["status"] == "cancelled"
    assert not service.has_active()
    assert service.list() == [state]


def test_acquisition_list_places_active_latest_attempt_last_despite_opaque_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_module = importlib.import_module("chordatlas.acquisition.store")
    tokens = iter(["f" * 32, "a" * 32, "0" * 32, "b" * 32])
    clock = iter(
        f"2026-07-23T12:00:0{second}+00:00"
        for second in range(8)
    )
    monkeypatch.setattr(store_module.secrets, "token_hex", lambda _size: next(tokens))
    monkeypatch.setattr(store_module, "utc_now", lambda: next(clock))
    ProjectMediaStore.initialize(tmp_path)
    store = AcquisitionStore.initialize(tmp_path)
    older = store.create_request(
        normalized_url="https://audio.example/old",
        display_name="old.wav",
        retry_of=None,
    )
    store.append_event(older.id, "running", phase="receiving")
    store.append_event(
        older.id,
        "failed",
        phase="failed",
        failure_code="network_failed",
        failure_message="Failed safely.",
    )
    active = store.create_request(
        normalized_url="https://audio.example/new",
        display_name="new.wav",
        retry_of=None,
    )

    assert active.id < older.id
    assert store.list()[-1]["run_id"] == active.id
    assert store.list()[-1]["status"] == "queued"


def test_recovery_marks_interrupted_run_failed_without_resolver(tmp_path: Path) -> None:
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    request = service.store.create_request(
        normalized_url="https://audio.example/take",
        display_name="take.wav",
        retry_of=None,
    )
    recovered = AcquisitionStore.initialize(tmp_path).recover_interrupted()

    assert request.id in recovered
    state = service.store.status(request.id)
    assert state is not None
    assert state["status"] == "failed"
    assert state["failure_code"] == "acquisition_interrupted"


def test_private_locator_reader_rejects_hardlinks(tmp_path: Path) -> None:
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    request = service.store.create_request(
        normalized_url="https://audio.example/take?secret=private",
        display_name="take.wav",
        retry_of=None,
    )
    locator = service.store.private_root / f"{request.id}.json"
    linked = service.store.private_root / "linked-secret.json"
    os.link(locator, linked)

    with pytest.raises(AcquisitionError) as rejected:
        service.store.private_url(request.id)
    assert rejected.value.code == "acquisition_storage_integrity"


def test_forget_locator_preserves_history_and_disables_retry(tmp_path: Path) -> None:
    service = AcquisitionService(
        tmp_path,
        transport=FakeTransport(
            error=AcquisitionError("network_failed", "Download failed safely.")
        ),
    )
    run_id = service.start(
        url="https://audio.example/take?secret=private",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    assert service.wait(run_id, 5)["status"] == "failed"

    state = service.forget_locator(run_id)

    assert state["status"] == "failed"
    assert state["locator_retained"] is False
    assert service.store.load_request(run_id).id == run_id
    with pytest.raises(AcquisitionError) as retry:
        service.retry(run_id)
    assert retry.value.code == "locator_forgotten"


def test_recovery_repairs_published_source_without_duplicate(tmp_path: Path) -> None:
    media = ProjectMediaStore.initialize(tmp_path)
    store = AcquisitionStore.initialize(tmp_path)
    request = store.create_request(
        normalized_url="https://audio.example/take",
        display_name="take.wav",
        retry_of=None,
    )
    store.append_event(request.id, "running", phase="publishing")
    stage = media.allocate_private_stage()
    payload = synthetic_wav()
    stage.write_bytes(payload)
    os.chmod(stage, 0o600)
    source, asset = media.publish_staged_file(
        stage,
        byte_length=len(payload),
        display_name=request.display_name,
        authorization_confirmed=True,
        source_id=request.source_id,
        locator=RemoteLocator(
            source_id=request.source_id,
            channel="direct_https",
            private_locator="https://audio.example/take",
        ),
    )
    # Publication may consume the staging name immediately so no writable
    # hardlink remains alongside the immutable content-addressed blob.
    stage.unlink(missing_ok=True)

    recovered = AcquisitionService(tmp_path, transport=FakeTransport())
    state = recovered.status(request.id)

    assert state["status"] == "succeeded"
    assert state["output"] == {"source_id": source.id}
    assert len(recovered.media.list_public_sources()) == 1


def test_source_commit_wins_when_terminal_event_initially_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    original = service.store.append_event
    failed_once = False

    def flaky_append(run_id, status, **kwargs):
        nonlocal failed_once
        if status == "succeeded" and not failed_once:
            failed_once = True
            raise OSError("synthetic terminal-event failure")
        return original(run_id, status, **kwargs)

    monkeypatch.setattr(service.store, "append_event", flaky_append)
    run_id = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    state = service.wait(run_id, 5)

    assert state["status"] == "succeeded"
    assert len(service.media.list_public_sources()) == 1


def test_stage_replacement_fails_before_source_visibility_and_restart_is_clean(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service_module = importlib.import_module("chordatlas.acquisition.service")
    original = service_module._stage_asset_id
    replacement = bytearray(synthetic_wav())
    replacement[-1] ^= 0x01

    def swap_after_hash(path: Path) -> str:
        asset_id = original(path)
        path.unlink()
        path.write_bytes(replacement)
        os.chmod(path, 0o600)
        return asset_id

    monkeypatch.setattr(service_module, "_stage_asset_id", swap_after_hash)
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    run_id = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    state = service.wait(run_id, 5)

    assert state["status"] == "failed"
    assert state["failure_code"] == "stage_identity_changed"
    assert service.media.list_public_sources() == []
    monkeypatch.setattr(service_module, "_stage_asset_id", original)
    reopened = AcquisitionService(tmp_path, transport=FakeTransport())
    assert reopened.status(run_id)["status"] == "failed"


def test_consumed_stage_alias_cannot_mutate_published_blob(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_publish = ProjectMediaStore._publish_blob
    replacement = bytearray(synthetic_wav())
    replacement[-1] ^= 0x01

    def mutate_consumed_alias(self, stage_path, *args, **kwargs):
        consumed = original_publish(self, stage_path, *args, **kwargs)
        if consumed:
            assert not stage_path.exists()
            stage_path.write_bytes(replacement)
            os.chmod(stage_path, 0o600)
        return consumed

    monkeypatch.setattr(ProjectMediaStore, "_publish_blob", mutate_consumed_alias)
    original_payload = synthetic_wav()
    service = AcquisitionService(tmp_path, transport=FakeTransport(original_payload))
    run_id = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    state = service.wait(run_id, 5)

    assert state["status"] == "succeeded"
    assert service.media.audio_path_for_source(state["source_id"]).read_bytes() == original_payload


def test_permanent_terminal_event_failure_recovers_as_success_on_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AcquisitionService(tmp_path, transport=FakeTransport())
    original = service.store.append_event

    def fail_terminal(run_id, status, **kwargs):
        if status == "succeeded":
            raise OSError("persistent synthetic event failure")
        return original(run_id, status, **kwargs)

    monkeypatch.setattr(service.store, "append_event", fail_terminal)
    run_id = service.start(
        url="https://audio.example/take",
        display_name="take.wav",
        authorization_confirmed=True,
    )
    state = service.wait(run_id, 5)
    assert state["status"] == "running"
    assert len(service.media.list_public_sources()) == 1

    reopened = AcquisitionService(tmp_path, transport=FakeTransport())
    assert reopened.status(run_id)["status"] == "succeeded"
    assert len(reopened.media.list_public_sources()) == 1


def test_fixture_manifest_is_authorized_and_contains_no_recording() -> None:
    path = Path(__file__).parent / "fixtures" / "acquisition" / "fixture-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["fixture_policy"] == "generated-in-test-only"
    assert manifest["review"] == {
        "contains_copyrighted_lyrics": False,
        "contains_third_party_recording": False,
        "redistribution_authorized": True,
    }
