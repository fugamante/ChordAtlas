from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
_SOURCE_RE = re.compile(r"^src_[0-9a-f]{32}$")


class MediaImportError(ValueError):
    """A stable, public-safe media import failure."""

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
class FrameRange:
    """A half-open range in authoritative PCM sample frames."""

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        if self.start_frame < 0:
            raise ValueError("start_frame must be non-negative")
        if self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be greater than start_frame")

    @property
    def length_frames(self) -> int:
        return self.end_frame - self.start_frame

    def to_mapping(self) -> dict[str, int]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
        }


@dataclass(frozen=True)
class Timebase:
    """Integer-frame timebase shared by waveform, playback, and future chords."""

    sample_rate: int
    duration_frames: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.duration_frames <= 0:
            raise ValueError("duration_frames must be positive")

    def clamp_frame(self, frame: int) -> int:
        return min(max(int(frame), 0), self.duration_frames)

    def seconds_to_frame(self, seconds: int | float | str | Decimal) -> int:
        try:
            value = Decimal(str(seconds))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("seconds must be finite and numeric") from error
        if not value.is_finite():
            raise ValueError("seconds must be finite and numeric")
        frame = int((value * self.sample_rate).to_integral_value(rounding=ROUND_HALF_UP))
        return self.clamp_frame(frame)

    def frame_to_seconds(self, frame: int) -> float:
        return self.clamp_frame(frame) / self.sample_rate

    def validate_range(self, value: FrameRange) -> FrameRange:
        if value.end_frame > self.duration_frames:
            raise ValueError("frame range exceeds media duration")
        return value

    def to_mapping(self) -> dict[str, Any]:
        return {
            "unit": "sample_frame",
            "sample_rate": self.sample_rate,
            "duration_frames": self.duration_frames,
        }


@dataclass(frozen=True)
class MediaAsset:
    """Immutable content-addressed technical metadata for one validated WAV."""

    id: str
    sha256: str
    byte_length: int
    container: str
    codec: str
    sample_rate: int
    channels: int
    sample_width_bytes: int
    duration_frames: int

    def __post_init__(self) -> None:
        match = _ASSET_RE.fullmatch(self.id)
        if match is None or match.group(1) != self.sha256:
            raise ValueError("MediaAsset.id must match its SHA-256 digest")
        if _DIGEST_RE.fullmatch(self.sha256) is None:
            raise ValueError("MediaAsset.sha256 must be lowercase hexadecimal")
        if self.byte_length <= 0:
            raise ValueError("MediaAsset.byte_length must be positive")
        if self.container != "riff_wave" or self.codec != "pcm_s16le":
            raise ValueError("MediaAsset must use the Stage 1 PCM16 WAV contract")
        if self.channels not in (1, 2):
            raise ValueError("MediaAsset.channels must be mono or stereo")
        if self.sample_width_bytes != 2:
            raise ValueError("MediaAsset.sample_width_bytes must be 2")
        Timebase(self.sample_rate, self.duration_frames)

    @property
    def timebase(self) -> Timebase:
        return Timebase(self.sample_rate, self.duration_frames)

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "media_schema_version": "1.0.0-draft",
            "id": self.id,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "container": self.container,
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width_bytes": self.sample_width_bytes,
            "duration_frames": self.duration_frames,
        }

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "container": self.container,
            "codec": self.codec,
            "byte_length": self.byte_length,
            "channels": self.channels,
            "sample_width_bytes": self.sample_width_bytes,
            "timebase": self.timebase.to_mapping(),
            "duration_seconds": self.timebase.frame_to_seconds(self.duration_frames),
        }

    @classmethod
    def from_record_mapping(cls, value: dict[str, Any]) -> MediaAsset:
        return cls(
            id=str(value["id"]),
            sha256=str(value["sha256"]),
            byte_length=int(value["byte_length"]),
            container=str(value["container"]),
            codec=str(value["codec"]),
            sample_rate=int(value["sample_rate"]),
            channels=int(value["channels"]),
            sample_width_bytes=int(value["sample_width_bytes"]),
            duration_frames=int(value["duration_frames"]),
        )


@dataclass(frozen=True)
class SourceReference:
    """A safe source assertion kept separate from deduplicated media identity."""

    id: str
    asset_id: str
    display_name: str
    authorization_basis: str
    captured_at: str

    def __post_init__(self) -> None:
        if _SOURCE_RE.fullmatch(self.id) is None:
            raise ValueError("SourceReference.id is invalid")
        if _ASSET_RE.fullmatch(self.asset_id) is None:
            raise ValueError("SourceReference.asset_id is invalid")
        if not self.display_name or len(self.display_name) > 160:
            raise ValueError("SourceReference.display_name is invalid")
        if self.authorization_basis != "user_attested_authorized":
            raise ValueError("SourceReference.authorization_basis is invalid")

    def to_record_mapping(self) -> dict[str, Any]:
        return {
            "source_schema_version": "1.0.0-draft",
            "id": self.id,
            "asset_id": self.asset_id,
            "display_name": self.display_name,
            "authorization_basis": self.authorization_basis,
            "captured_at": self.captured_at,
        }

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "authorization_basis": self.authorization_basis,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_record_mapping(cls, value: dict[str, Any]) -> SourceReference:
        return cls(
            id=str(value["id"]),
            asset_id=str(value["asset_id"]),
            display_name=str(value["display_name"]),
            authorization_basis=str(value["authorization_basis"]),
            captured_at=str(value["captured_at"]),
        )


@dataclass(frozen=True)
class LocalLocator:
    """Private import-channel metadata that is never returned by public APIs."""

    source_id: str
    channel: str
    private_locator: str

    def __post_init__(self) -> None:
        if _SOURCE_RE.fullmatch(self.source_id) is None:
            raise ValueError("LocalLocator.source_id is invalid")
        if self.channel != "browser_upload":
            raise ValueError("LocalLocator.channel is invalid")
        if not self.private_locator:
            raise ValueError("LocalLocator.private_locator is required")

    def to_record_mapping(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "channel": self.channel,
            "private_locator": self.private_locator,
        }


@dataclass(frozen=True)
class RemoteLocator:
    """Private direct-HTTPS locator that is never returned by public APIs."""

    source_id: str
    channel: str
    private_locator: str

    def __post_init__(self) -> None:
        if _SOURCE_RE.fullmatch(self.source_id) is None:
            raise ValueError("RemoteLocator.source_id is invalid")
        if self.channel != "direct_https":
            raise ValueError("RemoteLocator.channel is invalid")
        if not self.private_locator.startswith("https://"):
            raise ValueError("RemoteLocator.private_locator must use HTTPS")

    def to_record_mapping(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "channel": self.channel,
            "private_locator": self.private_locator,
        }


@dataclass(frozen=True)
class WaveformBucket:
    start_frame: int
    end_frame: int
    min_q15: int
    max_q15: int

    def __post_init__(self) -> None:
        FrameRange(self.start_frame, self.end_frame)
        if not (-32768 <= self.min_q15 <= self.max_q15 <= 32767):
            raise ValueError("waveform peaks must be signed Q15 values")

    def to_mapping(self) -> dict[str, int]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "min_q15": self.min_q15,
            "max_q15": self.max_q15,
        }


@dataclass(frozen=True)
class Waveform:
    algorithm: str
    bucket_frames: int
    timebase: Timebase
    buckets: tuple[WaveformBucket, ...]

    def __post_init__(self) -> None:
        if self.algorithm != "pcm16-folded-peak-v1":
            raise ValueError("unsupported waveform algorithm")
        if self.bucket_frames <= 0:
            raise ValueError("bucket_frames must be positive")
        expected = 0
        for bucket in self.buckets:
            if bucket.start_frame != expected:
                raise ValueError("waveform buckets must be contiguous")
            expected = bucket.end_frame
        if expected != self.timebase.duration_frames:
            raise ValueError("waveform buckets must cover the complete asset")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "waveform_schema_version": "1.0.0-draft",
            "algorithm": self.algorithm,
            "bucket_frames": self.bucket_frames,
            "timebase": self.timebase.to_mapping(),
            "buckets": [bucket.to_mapping() for bucket in self.buckets],
        }
