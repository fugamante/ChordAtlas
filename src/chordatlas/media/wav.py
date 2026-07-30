from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import BinaryIO

from chordatlas.media.models import (
    MediaImportError,
    Timebase,
    Waveform,
    WaveformBucket,
)

MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000
MAX_DURATION_SECONDS = 2 * 60 * 60
DEFAULT_BUCKET_FRAMES = 1024
MAX_WAVEFORM_BUCKETS = 16_384


@dataclass(frozen=True)
class PcmWavInfo:
    sample_rate: int
    channels: int
    sample_width_bytes: int
    duration_frames: int

    @property
    def timebase(self) -> Timebase:
        return Timebase(self.sample_rate, self.duration_frames)


def inspect_pcm16_wav(path: Path) -> PcmWavInfo:
    """Validate the intentionally narrow Stage 1 RIFF/WAVE PCM16 contract."""

    try:
        with path.open("rb") as handle:
            return inspect_pcm16_wav_stream(handle, file_size=path.stat().st_size)
    except MediaImportError:
        raise
    except (EOFError, OSError, wave.Error, struct.error):
        raise _invalid("invalid_wav", "The WAV file could not be decoded.") from None


def inspect_pcm16_wav_stream(handle: BinaryIO, *, file_size: int) -> PcmWavInfo:
    """Validate PCM16 WAV metadata and bytes through one already-open descriptor."""

    try:
        handle.seek(0)
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise _invalid("unsupported_container", "Select a RIFF/WAVE audio file.")
        riff_size = struct.unpack("<I", header[4:8])[0]
        if riff_size + 8 > file_size:
            raise _invalid("truncated_wav", "The WAV file is truncated.")

        fmt: tuple[int, int, int, int, int, int] | None = None
        data_size: int | None = None
        while handle.tell() + 8 <= file_size:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                break
            chunk_id = chunk_header[:4]
            chunk_size = struct.unpack("<I", chunk_header[4:])[0]
            chunk_start = handle.tell()
            chunk_end = chunk_start + chunk_size
            if chunk_end > file_size:
                raise _invalid("truncated_wav", "The WAV file contains a truncated chunk.")
            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    raise _invalid("invalid_wav", "The WAV format chunk is invalid.")
                payload = handle.read(16)
                fmt = struct.unpack("<HHIIHH", payload)
            elif chunk_id == b"data":
                data_size = chunk_size
            handle.seek(chunk_end + (chunk_size & 1))

        if fmt is None or data_size is None:
            raise _invalid("invalid_wav", "The WAV file is missing audio format or data.")
        format_tag, channels, sample_rate, byte_rate, block_align, bits = fmt
        if format_tag != 1:
            raise _invalid(
                "unsupported_codec",
                "Stage 1 supports uncompressed integer PCM WAV files only.",
            )
        if channels not in (1, 2):
            raise _invalid("unsupported_channels", "Use a mono or stereo WAV file.")
        if bits != 16:
            raise _invalid("unsupported_sample_width", "Use a 16-bit PCM WAV file.")
        if not (MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE):
            raise _invalid(
                "unsupported_sample_rate",
                "Use a WAV sample rate between 8 kHz and 192 kHz.",
            )
        expected_block_align = channels * 2
        if block_align != expected_block_align:
            raise _invalid("invalid_wav", "The WAV block alignment is inconsistent.")
        if byte_rate != sample_rate * block_align:
            raise _invalid("invalid_wav", "The WAV byte rate is inconsistent.")
        if data_size == 0 or data_size % block_align:
            raise _invalid("invalid_wav", "The WAV audio data length is invalid.")
        duration_frames = data_size // block_align
        if duration_frames > sample_rate * MAX_DURATION_SECONDS:
            raise _invalid(
                "duration_limit",
                "The WAV duration exceeds the two-hour Stage 1 limit.",
            )

        handle.seek(0)
        with wave.open(handle, "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise _invalid(
                    "unsupported_codec",
                    "Stage 1 supports uncompressed integer PCM WAV files only.",
                )
            if (
                reader.getnchannels() != channels
                or reader.getsampwidth() != 2
                or reader.getframerate() != sample_rate
                or reader.getnframes() != duration_frames
            ):
                raise _invalid("invalid_wav", "The WAV metadata is contradictory.")
            remaining = duration_frames
            while remaining:
                requested = min(remaining, DEFAULT_BUCKET_FRAMES * 16)
                payload = reader.readframes(requested)
                actual = len(payload) // block_align
                if actual != requested:
                    raise _invalid("truncated_wav", "The WAV audio data is truncated.")
                remaining -= actual
        return PcmWavInfo(
            sample_rate=sample_rate,
            channels=channels,
            sample_width_bytes=2,
            duration_frames=duration_frames,
        )
    except MediaImportError:
        raise
    except (EOFError, OSError, wave.Error, struct.error):
        raise _invalid("invalid_wav", "The WAV file could not be decoded.") from None


def build_waveform(
    path: Path,
    info: PcmWavInfo,
    *,
    bucket_frames: int = DEFAULT_BUCKET_FRAMES,
) -> Waveform:
    try:
        with path.open("rb") as handle:
            return build_waveform_stream(handle, info, bucket_frames=bucket_frames)
    except MediaImportError:
        raise
    except (EOFError, OSError, wave.Error, struct.error):
        raise _invalid("decode_failed", "The WAV waveform could not be generated.") from None


def build_waveform_stream(
    handle: BinaryIO,
    info: PcmWavInfo,
    *,
    bucket_frames: int = DEFAULT_BUCKET_FRAMES,
) -> Waveform:
    if bucket_frames <= 0:
        raise ValueError("bucket_frames must be positive")

    buckets: list[WaveformBucket] = []
    position = 0
    try:
        handle.seek(0)
        with wave.open(handle, "rb") as reader:
            while position < info.duration_frames:
                expected = min(bucket_frames, info.duration_frames - position)
                raw = reader.readframes(expected)
                sample_count = len(raw) // 2
                if sample_count != expected * info.channels:
                    raise _invalid("truncated_wav", "The WAV audio data is truncated.")
                samples = struct.unpack(f"<{sample_count}h", raw)
                if info.channels == 1:
                    folded = samples
                else:
                    folded = tuple(
                        (samples[index] + samples[index + 1]) // 2
                        for index in range(0, len(samples), 2)
                    )
                end = position + expected
                buckets.append(
                    WaveformBucket(
                        start_frame=position,
                        end_frame=end,
                        min_q15=min(folded),
                        max_q15=max(folded),
                    )
                )
                position = end
    except MediaImportError:
        raise
    except (EOFError, OSError, wave.Error, struct.error):
        raise _invalid("decode_failed", "The WAV waveform could not be generated.") from None

    return Waveform(
        algorithm="pcm16-folded-peak-v1",
        bucket_frames=bucket_frames,
        timebase=info.timebase,
        buckets=tuple(buckets),
    )


def waveform_bucket_frames(duration_frames: int) -> int:
    """Choose a deterministic resolution whose JSON remains safely bounded."""

    if duration_frames <= 0:
        raise ValueError("duration_frames must be positive")
    return max(DEFAULT_BUCKET_FRAMES, ceil(duration_frames / MAX_WAVEFORM_BUCKETS))


def _invalid(code: str, message: str) -> MediaImportError:
    return MediaImportError(code, message, retryable=True)
