from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_ACQUISITION_RE = re.compile(r"^acq_[0-9a-f]{32}$")
_SOURCE_RE = re.compile(r"^src_[0-9a-f]{32}$")
_HOSTED_MEDIA_HOSTS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "soundcloud.com",
    "spotify.com",
    "bandcamp.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
)


class AcquisitionError(ValueError):
    """A stable error that is safe to expose without its private URL."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable

    def to_mapping(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.public_message,
                "retryable": self.retryable,
            }
        }


@dataclass(frozen=True)
class DirectHttpsSource:
    """A private normalized direct-media URL. Never serialize this publicly."""

    url: str
    hostname: str
    target: str

    @classmethod
    def classify(cls, value: str) -> DirectHttpsSource:
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or not value.isascii()
            or "\\" in value
            or any(character.isspace() for character in value)
        ):
            raise _invalid_source()
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise _invalid_source()
        try:
            split = urlsplit(value)
            port = split.port
        except ValueError:
            raise _invalid_source() from None
        if (
            split.scheme.lower() != "https"
            or not split.hostname
            or split.username is not None
            or split.password is not None
            or split.fragment
            or port not in (None, 443)
        ):
            raise _invalid_source()
        hostname = split.hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise AcquisitionError(
                "ip_literal_forbidden",
                "Enter a direct HTTPS media hostname, not an IP address.",
                retryable=False,
            )
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise _invalid_source() from None
        if (
            not hostname
            or len(hostname) > 253
            or "." not in hostname
            or hostname == "localhost"
            or hostname.endswith((".local", ".internal", ".home.arpa"))
            or any(
                hostname == blocked or hostname.endswith(f".{blocked}")
                for blocked in _HOSTED_MEDIA_HOSTS
            )
        ):
            raise AcquisitionError(
                "hosted_page_unsupported",
                "Hosted-platform pages are not supported. Use an authorized direct WAV URL.",
                retryable=False,
            )
        path = split.path or "/"
        normalized = urlunsplit(("https", hostname, path, split.query, ""))
        target = path + (f"?{split.query}" if split.query else "")
        return cls(normalized, hostname, target)


@dataclass(frozen=True)
class AcquisitionRequest:
    id: str
    source_id: str
    display_name: str
    url_fingerprint: str
    authorization_basis: str
    authorized_at: str
    created_at: str
    retry_of: str | None = None

    def __post_init__(self) -> None:
        if _ACQUISITION_RE.fullmatch(self.id) is None:
            raise ValueError("invalid acquisition id")
        if _SOURCE_RE.fullmatch(self.source_id) is None:
            raise ValueError("invalid source id")
        if not self.display_name or len(self.display_name) > 160:
            raise ValueError("invalid display name")
        if not re.fullmatch(r"[0-9a-f]{64}", self.url_fingerprint):
            raise ValueError("invalid URL fingerprint")
        if self.authorization_basis != "user_attested_authorized":
            raise ValueError("invalid authorization basis")
        if self.retry_of is not None and _ACQUISITION_RE.fullmatch(self.retry_of) is None:
            raise ValueError("invalid retry id")

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "acquisition_schema_version": "1.0.0-draft",
            "id": self.id,
            "source_id": self.source_id,
            "display_name": self.display_name,
            "url_fingerprint": self.url_fingerprint,
            "authorization_basis": self.authorization_basis,
            "authorized_at": self.authorized_at,
            "created_at": self.created_at,
            "retry_of": self.retry_of,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> AcquisitionRequest:
        return cls(
            id=str(value["id"]),
            source_id=str(value["source_id"]),
            display_name=str(value["display_name"]),
            url_fingerprint=str(value["url_fingerprint"]),
            authorization_basis=str(value["authorization_basis"]),
            authorized_at=str(value["authorized_at"]),
            created_at=str(value["created_at"]),
            retry_of=None if value["retry_of"] is None else str(value["retry_of"]),
        )


@dataclass(frozen=True)
class DownloadResult:
    byte_length: int
    final_url: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def same_origin(first: DirectHttpsSource, second: DirectHttpsSource) -> bool:
    return first.hostname == second.hostname


def _invalid_source() -> AcquisitionError:
    return AcquisitionError(
        "invalid_source",
        "Enter an authorized direct HTTPS URL to a WAV file.",
        retryable=False,
    )
