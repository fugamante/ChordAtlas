from __future__ import annotations

import http.client
import ipaddress
import os
import queue
import socket
import ssl
import stat
import threading
import time
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urljoin

from chordatlas.acquisition.models import (
    AcquisitionError,
    DirectHttpsSource,
    DownloadResult,
    same_origin,
)

DEFAULT_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_WAV_TYPES = {"audio/wav", "audio/x-wav", "audio/vnd.wave", "application/octet-stream"}
_NAT64_RANGES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
_SPECIAL_IPV4_RANGES = (
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
)


class AcquisitionTransport(Protocol):
    def download(
        self,
        source: DirectHttpsSource,
        stage_path: Path,
        *,
        progress: Callable[[int, int], None],
        cancelled: Callable[[], bool],
        max_bytes: int,
    ) -> DownloadResult: ...


class PinnedHttpsTransport:
    """Direct HTTPS with DNS answers pinned through TLS and the peer check."""

    def __init__(
        self,
        *,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        socket_factory: Callable[..., socket.socket] = socket.create_connection,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 15,
        max_redirects: int = 3,
    ) -> None:
        self._resolver = resolver
        self._socket_factory = socket_factory
        self._context = ssl_context or ssl.create_default_context()
        if (
            not self._context.check_hostname
            or self._context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise ValueError("acquisition TLS requires certificate and hostname verification")
        if hasattr(ssl, "TLSVersion"):
            self._context.minimum_version = max(
                self._context.minimum_version,
                ssl.TLSVersion.TLSv1_2,
            )
        self._context.set_alpn_protocols(["http/1.1"])
        self._timeout = timeout_seconds
        self._max_redirects = max_redirects
        self._resolver_slot = threading.BoundedSemaphore(1)

    def download(
        self,
        source: DirectHttpsSource,
        stage_path: Path,
        *,
        progress: Callable[[int, int], None],
        cancelled: Callable[[], bool],
        max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> DownloadResult:
        current = source
        deadline = time.monotonic() + self._timeout
        for redirect_count in range(self._max_redirects + 1):
            _check_deadline(deadline, cancelled)
            addresses = self._safe_addresses(
                current.hostname,
                deadline=deadline,
                cancelled=cancelled,
            )
            response, connection = self._request(
                current,
                addresses[0],
                timeout=max(0.1, deadline - time.monotonic()),
            )
            try:
                if response.status in _REDIRECTS:
                    if redirect_count >= self._max_redirects:
                        raise AcquisitionError(
                            "redirect_limit",
                            "The direct media URL redirected too many times.",
                            retryable=False,
                        )
                    locations = response.headers.get_all("Location", [])
                    if len(locations) != 1:
                        raise AcquisitionError("invalid_redirect", "The media redirect is invalid.")
                    redirected = DirectHttpsSource.classify(urljoin(current.url, locations[0]))
                    if not same_origin(current, redirected):
                        raise AcquisitionError(
                            "cross_origin_redirect",
                            "The direct media URL redirected to another site.",
                            retryable=False,
                        )
                    current = redirected
                    continue
                if response.status != 200:
                    raise AcquisitionError(
                        "http_status",
                        "The media server did not return a successful direct-media response.",
                        retryable=response.status >= 500,
                    )
                length = _content_length(response)
                if length > max_bytes:
                    raise AcquisitionError(
                        "download_too_large",
                        (
                            f"The media exceeds the {max_bytes // (1024 * 1024)} MiB limit. "
                            "Download or convert it outside ChordAtlas, then use Local WAV."
                        ),
                        retryable=False,
                    )
                if response.headers.get_all("Transfer-Encoding", []):
                    raise AcquisitionError(
                        "ambiguous_framing",
                        "The media server returned unsupported response framing.",
                        retryable=False,
                    )
                encodings = response.headers.get_all("Content-Encoding", [])
                if len(encodings) > 1 or (encodings and encodings[0].lower() != "identity"):
                    raise AcquisitionError(
                        "encoded_response",
                        "The media server returned an encoded response.",
                        retryable=False,
                    )
                content_types = response.headers.get_all("Content-Type", [])
                if len(content_types) != 1:
                    raise AcquisitionError(
                        "ambiguous_content_type",
                        "The media server returned an ambiguous content type.",
                        retryable=False,
                    )
                content_type = content_types[0].split(";", 1)[0].strip().lower()
                if content_type not in _WAV_TYPES:
                    raise AcquisitionError(
                        "unsupported_content_type",
                        "The URL did not return direct WAV media.",
                        retryable=False,
                    )
                written = 0
                flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(stage_path, flags)
                entry = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) != 0o600
                    or entry.st_nlink != 1
                ):
                    os.close(descriptor)
                    raise AcquisitionError(
                        "unsafe_stage",
                        "The private acquisition stage failed an integrity check.",
                        retryable=False,
                    )
                with os.fdopen(descriptor, "wb") as output:
                    while written < length:
                        _check_deadline(deadline, cancelled)
                        connection.settimeout(max(0.1, deadline - time.monotonic()))
                        payload = response.read(min(_CHUNK_BYTES, length - written))
                        if not payload:
                            raise AcquisitionError(
                                "download_truncated",
                                "The media response ended before its declared length.",
                            )
                        output.write(payload)
                        written += len(payload)
                        progress(written, length)
                    output.flush()
                    os.fsync(output.fileno())
                return DownloadResult(written, current.url)
            finally:
                connection.close()
        raise AssertionError("redirect loop escaped its bound")

    def _safe_addresses(
        self,
        hostname: str,
        *,
        deadline: float | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[str, ...]:
        if deadline is None:
            deadline = time.monotonic() + self._timeout
        result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        while not self._resolver_slot.acquire(blocking=False):
            _check_deadline(deadline, cancelled)
            time.sleep(min(0.01, max(0.001, deadline - time.monotonic())))

        def resolve() -> None:
            try:
                result.put((True, self._resolver(hostname, 443, type=socket.SOCK_STREAM)))
            except OSError as error:
                result.put((False, error))
            finally:
                self._resolver_slot.release()

        thread = threading.Thread(target=resolve, name="acquisition-resolver", daemon=True)
        thread.start()
        while True:
            _check_deadline(deadline, cancelled)
            try:
                ok, payload = result.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                break
            except queue.Empty:
                continue
        if not ok:
            raise AcquisitionError("dns_failed", "The media hostname could not be resolved.")
        answers = payload
        if not isinstance(answers, list):
            raise AcquisitionError("unsafe_dns", "The media hostname resolved unsafely.")
        values: set[str] = set()
        for answer in answers:
            raw = str(answer[4][0]).split("%", 1)[0]
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                raise AcquisitionError("unsafe_dns", "The media hostname resolved unsafely.") from None
            if not _is_safe_global(address):
                raise AcquisitionError(
                    "unsafe_dns",
                    "The media hostname resolved to a non-public address.",
                    retryable=False,
                )
            values.add(str(address))
        if not values or len(values) > 16:
            raise AcquisitionError("unsafe_dns", "The media hostname resolved unsafely.")
        return tuple(sorted(values))

    def _request(
        self,
        source: DirectHttpsSource,
        address: str,
        *,
        timeout: float,
    ) -> tuple[http.client.HTTPResponse, ssl.SSLSocket]:
        raw: socket.socket | None = None
        connection: ssl.SSLSocket | None = None
        try:
            raw = self._socket_factory((address, 443), timeout=timeout)
            connection = self._context.wrap_socket(raw, server_hostname=source.hostname)
            connection.settimeout(timeout)
            peer = ipaddress.ip_address(str(connection.getpeername()[0]).split("%", 1)[0])
            expected = ipaddress.ip_address(address)
            if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
                peer = peer.ipv4_mapped
            if peer != expected:
                raise AcquisitionError(
                    "peer_mismatch",
                    "The HTTPS peer did not match the validated address.",
                    retryable=False,
                )
            request = (
                f"GET {source.target} HTTP/1.1\r\n"
                f"Host: {source.hostname}\r\n"
                "Accept: audio/wav, audio/x-wav, application/octet-stream\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request)
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response, connection
        except AcquisitionError:
            if connection is not None:
                connection.close()
            elif raw is not None:
                raw.close()
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException):
            if connection is not None:
                connection.close()
            elif raw is not None:
                raw.close()
            raise AcquisitionError(
                "network_failed",
                "The direct media download failed securely. Retry the acquisition.",
            ) from None


def _is_safe_global(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return not any(address in network for network in _SPECIAL_IPV4_RANGES)
    if isinstance(address, ipaddress.IPv6Address):
        if (
            address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or any(address in network for network in _NAT64_RANGES)
        ):
            return False
    return True


def _content_length(response: http.client.HTTPResponse) -> int:
    values = response.headers.get_all("Content-Length", [])
    if len(values) != 1:
        raise AcquisitionError(
            "content_length_required",
            "The media server must provide one bounded Content-Length.",
            retryable=False,
        )
    try:
        length = int(values[0])
    except ValueError:
        raise AcquisitionError(
            "invalid_content_length",
            "The media server returned an invalid Content-Length.",
            retryable=False,
        ) from None
    if length <= 0:
        raise AcquisitionError("empty_download", "The media response was empty.")
    return length


def _check_deadline(deadline: float, cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise AcquisitionError("acquisition_cancelled", "Acquisition cancelled.")
    if time.monotonic() >= deadline:
        raise AcquisitionError(
            "acquisition_timeout",
            "The direct media download exceeded its time limit. Retry it.",
        )
