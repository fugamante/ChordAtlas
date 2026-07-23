from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from chordatlas.media.models import (
    LocalLocator,
    MediaAsset,
    MediaImportError,
    RemoteLocator,
    SourceReference,
    Waveform,
    WaveformBucket,
)
from chordatlas.media.wav import (
    MAX_WAVEFORM_BUCKETS,
    build_waveform,
    inspect_pcm16_wav,
    waveform_bucket_frames,
)

DEFAULT_MAX_UPLOAD_BYTES = 128 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_MAX_RECORD_BYTES = 4 * 1024 * 1024


class ProjectMediaStore:
    """Project-private immutable media storage with manifest-last publication."""

    def __init__(self, project_root: Path) -> None:
        resolved = project_root.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(f"project root is not a directory: {resolved}")
        self.project_root = resolved
        self.root = resolved / ".chordatlas"
        self.media_root = self.root / "media"
        self.blobs_base = self.media_root / "blobs"
        self.blobs_root = self.media_root / "blobs" / "sha256"
        self.manifests_base = self.media_root / "manifests"
        self.manifests_root = self.media_root / "manifests" / "sha256"
        self.sources_root = self.root / "sources"
        self.private_root = self.root / "private"
        self.locators_root = self.root / "private" / "locators"
        self.cache_root = self.root / "cache"
        self.waveforms_root = self.root / "cache" / "waveforms"
        self.tmp_root = self.root / "tmp"
        self._publish_lock = threading.Lock()

    @classmethod
    def initialize(cls, project_root: Path) -> ProjectMediaStore:
        store = cls(project_root)
        for path in (
            store.root,
            store.media_root,
            store.blobs_base,
            store.blobs_root,
            store.manifests_base,
            store.manifests_root,
            store.sources_root,
            store.private_root,
            store.locators_root,
            store.cache_root,
            store.waveforms_root,
            store.tmp_root,
        ):
            _ensure_private_directory(path)
        store._ensure_storage_ignore()
        return store

    @classmethod
    def open(cls, project_root: Path) -> ProjectMediaStore:
        store = cls(project_root)
        for path in (
            store.root,
            store.media_root,
            store.blobs_base,
            store.blobs_root,
            store.manifests_base,
            store.manifests_root,
            store.sources_root,
            store.private_root,
            store.locators_root,
            store.cache_root,
            store.waveforms_root,
            store.tmp_root,
        ):
            _require_private_directory(path)
        store._ensure_storage_ignore()
        return store

    def import_stream(
        self,
        stream: BinaryIO,
        *,
        byte_length: int,
        display_name: str,
        authorization_confirmed: bool,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> tuple[SourceReference, MediaAsset]:
        if not authorization_confirmed:
            raise MediaImportError(
                "authorization_required",
                "Confirm that you are authorized to process this recording.",
                retryable=True,
            )
        if byte_length <= 0:
            raise MediaImportError("empty_upload", "Choose a non-empty WAV file.")
        if byte_length > max_upload_bytes:
            raise MediaImportError(
                "upload_too_large",
                f"The file exceeds the {max_upload_bytes // (1024 * 1024)} MiB limit.",
            )
        stage_path = self._allocate_stage()
        written = 0
        try:
            with stage_path.open("wb") as handle:
                os.chmod(stage_path, 0o600)
                remaining = byte_length
                while remaining:
                    payload = stream.read(min(_CHUNK_BYTES, remaining))
                    if not payload:
                        raise MediaImportError(
                            "upload_truncated",
                            "The upload ended before the declared file length.",
                        )
                    handle.write(payload)
                    written += len(payload)
                    remaining -= len(payload)
                handle.flush()
                os.fsync(handle.fileno())

            if written != byte_length:
                raise MediaImportError("upload_truncated", "The upload length is inconsistent.")

            safe_name = _safe_display_name(display_name)
            source_id = f"src_{secrets.token_hex(16)}"
            return self.publish_staged_file(
                stage_path,
                byte_length=byte_length,
                display_name=safe_name,
                authorization_confirmed=True,
                source_id=source_id,
                locator=LocalLocator(
                    source_id=source_id,
                    channel="browser_upload",
                    private_locator=f"browser-upload:{safe_name}",
                ),
                max_upload_bytes=max_upload_bytes,
            )
        finally:
            try:
                stage_path.unlink()
            except FileNotFoundError:
                pass

    def allocate_private_stage(self) -> Path:
        """Allocate a private project stage for a supervised ingestion adapter."""

        return self._allocate_stage()

    def publish_staged_file(
        self,
        stage_path: Path,
        *,
        byte_length: int,
        display_name: str,
        authorization_confirmed: bool,
        source_id: str,
        locator: LocalLocator | RemoteLocator,
        expected_asset_id: str | None = None,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> tuple[SourceReference, MediaAsset]:
        """Validate and atomically publish a complete private PCM16-WAV stage."""

        if not authorization_confirmed:
            raise MediaImportError(
                "authorization_required",
                "Confirm that you are authorized to process this recording.",
            )
        _validate_source_id(source_id)
        if locator.source_id != source_id:
            raise MediaImportError(
                "invalid_source_id",
                "The private locator does not match the media source.",
                retryable=False,
            )
        if byte_length <= 0:
            raise MediaImportError("empty_upload", "Choose a non-empty WAV file.")
        if byte_length > max_upload_bytes:
            raise MediaImportError(
                "upload_too_large",
                f"The file exceeds the {max_upload_bytes // (1024 * 1024)} MiB limit.",
            )
        resolved_stage = stage_path.resolve(strict=True)
        if resolved_stage.parent != self.tmp_root.resolve(strict=True):
            raise MediaImportError(
                "unsafe_storage_path",
                "The staged media file is outside project-private storage.",
                retryable=False,
            )
        if stage_path.is_symlink() or not stage_path.is_file():
            raise _integrity_error()
        _require_private_file(stage_path)
        stage_entry = stage_path.stat()
        if stage_entry.st_nlink != 1:
            raise _integrity_error()
        if stage_entry.st_size != byte_length:
            raise MediaImportError("upload_truncated", "The upload length is inconsistent.")
        stage_identity = (
            stage_entry.st_dev,
            stage_entry.st_ino,
            stage_entry.st_mtime_ns,
            stage_entry.st_ctime_ns,
        )

        digest = hashlib.sha256()
        with stage_path.open("rb") as handle:
            for payload in iter(lambda: handle.read(_CHUNK_BYTES), b""):
                digest.update(payload)
        _require_stage_identity(stage_path, stage_identity, byte_length)
        hex_digest = digest.hexdigest()
        asset_id = f"sha256:{hex_digest}"
        if expected_asset_id is not None and asset_id != expected_asset_id:
            raise MediaImportError(
                "stage_identity_changed",
                "The private acquisition stage changed during validation.",
                retryable=False,
            )
        _require_stage_identity(stage_path, stage_identity, byte_length)
        info = inspect_pcm16_wav(stage_path)
        _require_stage_identity(stage_path, stage_identity, byte_length)
        bucket_frames = waveform_bucket_frames(info.duration_frames)
        waveform = build_waveform(stage_path, info, bucket_frames=bucket_frames)
        _require_stage_identity(stage_path, stage_identity, byte_length)

        with self._publish_lock:
            blob_path = self._blob_path(hex_digest, create_parent=True)
            manifest_path = self._manifest_path(hex_digest, create_parent=True)
            waveform_path = self._waveform_path(
                hex_digest,
                bucket_frames=bucket_frames,
                create_parent=True,
            )
            existing = self._load_existing_asset(manifest_path, blob_path, hex_digest)
            if existing is None:
                stage_consumed = self._publish_blob(
                    stage_path,
                    blob_path,
                    hex_digest,
                    byte_length,
                    stage_identity=stage_identity,
                )
                if not stage_consumed:
                    _require_stage_identity(stage_path, stage_identity, byte_length)
                asset = MediaAsset(
                    id=asset_id,
                    sha256=hex_digest,
                    byte_length=byte_length,
                    container="riff_wave",
                    codec="pcm_s16le",
                    sample_rate=info.sample_rate,
                    channels=info.channels,
                    sample_width_bytes=info.sample_width_bytes,
                    duration_frames=info.duration_frames,
                )
                self._publish_waveform(waveform_path, waveform)
                self._publish_json_exclusive(
                    manifest_path,
                    asset.to_record_mapping(),
                    collision_code="asset_manifest_collision",
                )
            else:
                asset = existing
                self._verify_info(asset, info)
                self._publish_waveform(waveform_path, waveform)

            source = SourceReference(
                id=source_id,
                asset_id=asset.id,
                display_name=_safe_display_name(display_name),
                authorization_basis="user_attested_authorized",
                captured_at=_utc_now(),
            )
            self._publish_json_exclusive(
                self.locators_root / f"{source.id}.json",
                locator.to_record_mapping(),
                collision_code="source_id_collision",
            )
            self._publish_json_exclusive(
                self.sources_root / f"{source.id}.json",
                source.to_record_mapping(),
                collision_code="source_id_collision",
            )
        return source, asset

    def list_public_sources(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.sources_root.glob("src_*.json")):
            if path.is_symlink() or not path.is_file():
                raise MediaImportError(
                    "storage_integrity",
                    "Project media records failed an integrity check.",
                    retryable=False,
                )
            try:
                source = SourceReference.from_record_mapping(self._read_json(path))
            except MediaImportError:
                raise
            except (KeyError, TypeError, ValueError):
                raise _integrity_error() from None
            asset = self.asset_for_source(source.id)
            values.append(
                {
                    "source": source.to_public_mapping(),
                    "asset": asset.to_public_mapping(),
                }
            )
        return values

    def source(self, source_id: str) -> SourceReference:
        _validate_source_id(source_id)
        path = self.sources_root / f"{source_id}.json"
        try:
            return SourceReference.from_record_mapping(self._read_json(path))
        except MediaImportError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def asset_for_source(self, source_id: str) -> MediaAsset:
        source = self.source(source_id)
        digest = _asset_digest(source.asset_id)
        manifest_path = self._manifest_path(digest)
        blob_path = self._blob_path(digest)
        asset = self._load_existing_asset(manifest_path, blob_path, digest)
        if asset is None:
            raise MediaImportError(
                "storage_integrity",
                "The imported media asset is incomplete.",
                retryable=False,
            )
        return asset

    def audio_path_for_source(self, source_id: str) -> Path:
        asset = self.asset_for_source(source_id)
        return self._blob_path(asset.sha256)

    def waveform_for_source(self, source_id: str) -> Waveform:
        asset = self.asset_for_source(source_id)
        bucket_frames = waveform_bucket_frames(asset.duration_frames)
        path = self._waveform_path(asset.sha256, bucket_frames=bucket_frames)
        if not path.exists():
            info = inspect_pcm16_wav(self._blob_path(asset.sha256))
            self._publish_waveform(
                path,
                build_waveform(
                    self._blob_path(asset.sha256),
                    info,
                    bucket_frames=bucket_frames,
                ),
            )
        try:
            value = self._read_json(path)
            timebase_value = value["timebase"]
            buckets = tuple(
                WaveformBucket(
                    start_frame=int(item["start_frame"]),
                    end_frame=int(item["end_frame"]),
                    min_q15=int(item["min_q15"]),
                    max_q15=int(item["max_q15"]),
                )
                for item in value["buckets"]
            )
            from chordatlas.media.models import Timebase

            waveform = Waveform(
                algorithm=str(value["algorithm"]),
                bucket_frames=int(value["bucket_frames"]),
                timebase=Timebase(
                    sample_rate=int(timebase_value["sample_rate"]),
                    duration_frames=int(timebase_value["duration_frames"]),
                ),
                buckets=buckets,
            )
        except MediaImportError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        if (
            waveform.timebase != asset.timebase
            or waveform.bucket_frames != bucket_frames
            or len(waveform.buckets) > MAX_WAVEFORM_BUCKETS
        ):
            raise _integrity_error()
        return waveform

    def prune_waveform_cache(self, source_id: str) -> bool:
        asset = self.asset_for_source(source_id)
        path = self._waveform_path(
            asset.sha256,
            bucket_frames=waveform_bucket_frames(asset.duration_frames),
        )
        if path.is_symlink():
            raise MediaImportError(
                "storage_integrity",
                "The waveform cache failed an integrity check.",
                retryable=False,
            )
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def forget_remote_locator(self, source_id: str) -> bool:
        """Remove a Stage 5 URL without deleting its source or immutable media."""

        _validate_source_id(source_id)
        path = self.locators_root / f"{source_id}.json"
        if not path.exists():
            return False
        value = self._read_json(path)
        if value.get("source_id") != source_id or value.get("channel") != "direct_https":
            raise _integrity_error()
        path.unlink()
        _sync_directory(path.parent)
        return True

    def _allocate_stage(self) -> Path:
        _require_private_directory(self.tmp_root)
        descriptor, name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=self.tmp_root)
        os.close(descriptor)
        path = Path(name)
        os.chmod(path, 0o600)
        return path

    def _ensure_storage_ignore(self) -> None:
        path = self.root / ".gitignore"
        expected = "*\n!.gitignore\n"
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise _integrity_error()
            _require_private_file(path)
            if path.read_text(encoding="utf-8") != expected:
                raise _integrity_error()
            return
        self._publish_json_text_exclusive(path, expected)

    def _publish_json_text_exclusive(self, path: Path, payload: str) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=self.tmp_root)
        stage_path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(stage_path, path, follow_symlinks=False)
                _sync_directory(path.parent)
            except FileExistsError:
                if path.is_symlink() or not path.is_file():
                    raise _integrity_error() from None
                _require_private_file(path)
                if path.read_text(encoding="utf-8") != payload:
                    raise _integrity_error() from None
        finally:
            try:
                stage_path.unlink()
            except FileNotFoundError:
                pass

    def _blob_path(self, digest: str, *, create_parent: bool = False) -> Path:
        _validate_digest(digest)
        parent = self.blobs_root / digest[:2]
        if create_parent:
            _ensure_private_directory(parent)
        else:
            _require_private_directory(parent)
        return parent / f"{digest}.wav"

    def _manifest_path(self, digest: str, *, create_parent: bool = False) -> Path:
        _validate_digest(digest)
        parent = self.manifests_root / digest[:2]
        if create_parent:
            _ensure_private_directory(parent)
        else:
            _require_private_directory(parent)
        return parent / f"{digest}.json"

    def _waveform_path(
        self,
        digest: str,
        *,
        bucket_frames: int,
        create_parent: bool = False,
    ) -> Path:
        _validate_digest(digest)
        parent = self.waveforms_root / digest[:2]
        if create_parent:
            _ensure_private_directory(parent)
        else:
            _require_private_directory(parent)
        return parent / f"{digest}.pcm16-folded-peak-v1-{bucket_frames}.json"

    def _load_existing_asset(
        self,
        manifest_path: Path,
        blob_path: Path,
        digest: str,
    ) -> MediaAsset | None:
        if not manifest_path.exists():
            return None
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise _integrity_error()
        try:
            asset = MediaAsset.from_record_mapping(self._read_json(manifest_path))
        except MediaImportError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        if asset.sha256 != digest:
            raise _integrity_error()
        self._verify_blob(blob_path, digest, asset.byte_length)
        return asset

    def _publish_blob(
        self,
        stage_path: Path,
        blob_path: Path,
        digest: str,
        byte_length: int,
        *,
        stage_identity: tuple[int, int, int, int],
    ) -> bool:
        try:
            os.link(stage_path, blob_path, follow_symlinks=False)
            _sync_directory(blob_path.parent)
        except FileExistsError:
            self._verify_blob(blob_path, digest, byte_length)
            return False
        blob_entry = blob_path.stat()
        if (blob_entry.st_dev, blob_entry.st_ino) != stage_identity[:2]:
            blob_path.unlink()
            _sync_directory(blob_path.parent)
            raise MediaImportError(
                "stage_identity_changed",
                "The private acquisition stage changed during publication.",
                retryable=False,
            )
        stage_path.unlink()
        _sync_directory(stage_path.parent)
        try:
            self._verify_blob(blob_path, digest, byte_length)
        except Exception:
            blob_path.unlink(missing_ok=True)
            _sync_directory(blob_path.parent)
            raise
        return True

    def _verify_blob(self, path: Path, digest: str, byte_length: int) -> None:
        if path.is_symlink() or not path.is_file():
            raise _integrity_error()
        _require_private_file(path)
        file_stat = path.stat()
        for _ in range(20):
            if file_stat.st_nlink == 1:
                break
            time.sleep(0.001)
            file_stat = path.stat()
        if file_stat.st_nlink != 1:
            raise _integrity_error()
        if file_stat.st_size != byte_length:
            raise _integrity_error()
        observed = hashlib.sha256()
        with path.open("rb") as handle:
            for payload in iter(lambda: handle.read(_CHUNK_BYTES), b""):
                observed.update(payload)
        if observed.hexdigest() != digest:
            raise _integrity_error()

    def _publish_waveform(self, path: Path, waveform: Waveform) -> None:
        value = waveform.to_mapping()
        if path.exists():
            if path.is_symlink() or self._read_json(path) != value:
                raise _integrity_error()
            return
        self._publish_json_exclusive(
            path,
            value,
            collision_code="waveform_collision",
        )

    def _publish_json_exclusive(
        self,
        path: Path,
        value: dict[str, Any],
        *,
        collision_code: str,
    ) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        descriptor, name = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=self.tmp_root)
        stage_path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(stage_path, path, follow_symlinks=False)
                _sync_directory(path.parent)
            except FileExistsError:
                if path.is_symlink() or self._read_json(path) != value:
                    raise MediaImportError(
                        collision_code,
                        "Project media storage contains a conflicting immutable record.",
                        retryable=False,
                    ) from None
        finally:
            try:
                stage_path.unlink()
            except FileNotFoundError:
                pass

    def _read_json(self, path: Path) -> dict[str, Any]:
        value = None
        for attempt in range(20):
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except OSError:
                raise _integrity_error() from None
            try:
                entry = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) != 0o600
                    or entry.st_size > _MAX_RECORD_BYTES
                ):
                    raise _integrity_error()
                if entry.st_nlink != 1:
                    if attempt < 19:
                        os.close(descriptor)
                        descriptor = -1
                        time.sleep(0.001)
                        continue
                    raise _integrity_error()
                with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                    descriptor = -1
                    value = json.load(handle)
                break
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise _integrity_error() from None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        if not isinstance(value, dict):
            raise _integrity_error()
        return value

    @staticmethod
    def _verify_info(asset: MediaAsset, info) -> None:
        if (
            asset.sample_rate != info.sample_rate
            or asset.channels != info.channels
            or asset.sample_width_bytes != info.sample_width_bytes
            or asset.duration_frames != info.duration_frames
        ):
            raise _integrity_error()


def _safe_display_name(value: str) -> str:
    leaf = value.replace("\\", "/").split("/")[-1]
    leaf = "".join(character for character in leaf if character.isprintable())
    leaf = leaf.strip().strip(".")
    if not leaf:
        leaf = "audio.wav"
    return leaf[:160]


def _require_stage_identity(
    path: Path,
    expected: tuple[int, int, int, int],
    byte_length: int,
) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        raise _integrity_error() from None
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o600
        or entry.st_nlink != 1
        or entry.st_size != byte_length
        or (
            entry.st_dev,
            entry.st_ino,
            entry.st_mtime_ns,
            entry.st_ctime_ns,
        )
        != expected
    ):
        raise MediaImportError(
            "stage_identity_changed",
            "The private acquisition stage changed during validation.",
            retryable=False,
        )


def _ensure_private_directory(path: Path) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        entry = path.lstat()
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise MediaImportError(
            "unsafe_storage_path",
            "Project media storage contains an unsafe path.",
            retryable=False,
        )
    _require_private_mode(path, entry)


def _require_private_directory(path: Path) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        raise MediaImportError(
            "project_not_initialized",
            "Initialize the ChordAtlas media project before opening it.",
        ) from None
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise MediaImportError(
            "unsafe_storage_path",
            "Project media storage contains an unsafe path.",
            retryable=False,
        )
    _require_private_mode(path, entry)


def _require_private_mode(path: Path, entry: os.stat_result) -> None:
    if entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
        raise MediaImportError(
            "unsafe_storage_permissions",
            f"Project media storage must be owned by the current user with mode 0700: {path.name}",
            retryable=False,
        )


def _require_private_file(path: Path) -> None:
    entry = path.stat()
    if entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o600:
        raise MediaImportError(
            "unsafe_storage_permissions",
            f"Project media files must be owned by the current user with mode 0600: {path.name}",
            retryable=False,
        )


def _validate_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise MediaImportError("invalid_asset_id", "The media asset identifier is invalid.")


def _asset_digest(value: str) -> str:
    if not value.startswith("sha256:"):
        raise MediaImportError("invalid_asset_id", "The media asset identifier is invalid.")
    digest = value.removeprefix("sha256:")
    _validate_digest(digest)
    return digest


def _validate_source_id(value: str) -> None:
    suffix = value.removeprefix("src_")
    if not value.startswith("src_") or len(suffix) != 32:
        raise MediaImportError("invalid_source_id", "The media source identifier is invalid.")
    try:
        int(suffix, 16)
    except ValueError:
        raise MediaImportError("invalid_source_id", "The media source identifier is invalid.") from None


def _integrity_error() -> MediaImportError:
    return MediaImportError(
        "storage_integrity",
        "Project media storage failed an integrity check.",
        retryable=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
