from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from chordatlas._fs import ProjectAnchor, TargetOccupiedError, create_text_exclusive
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
    build_waveform_stream,
    inspect_pcm16_wav,
    inspect_pcm16_wav_stream,
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
        self._anchor: ProjectAnchor | None = None
        self._publish_lock = threading.Lock()

    @classmethod
    def initialize(cls, project_root: Path) -> ProjectMediaStore:
        store = cls(project_root)
        try:
            store._anchor = ProjectAnchor.for_project(store.project_root, create=True)
        except OSError:
            try:
                entry = store.root.lstat()
            except OSError:
                entry = None
            if entry is not None and stat.S_ISDIR(entry.st_mode):
                _require_private_mode(store.root, entry)
            raise MediaImportError(
                "unsafe_storage_path",
                "Project media storage contains an unsafe path.",
                retryable=False,
            ) from None
        for path in (
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
            _ensure_private_directory_anchored(path, store._anchor)
        store._ensure_storage_ignore()
        return store

    @classmethod
    def open(cls, project_root: Path) -> ProjectMediaStore:
        store = cls(project_root)
        try:
            store._anchor = ProjectAnchor.for_project(store.project_root)
        except OSError:
            raise MediaImportError(
                "project_not_initialized",
                "Initialize the ChordAtlas media project before opening it.",
            ) from None
        for path in (
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
            _require_private_directory_anchored(path, store._anchor)
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
        self._require_anchor()
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
        stage_handle = None
        written = 0
        try:
            stage_handle = self.open_private_stage(stage_path, writable=True)
            stage_handle.seek(0)
            stage_handle.truncate(0)
            remaining = byte_length
            while remaining:
                payload = stream.read(min(_CHUNK_BYTES, remaining))
                if not payload:
                    raise MediaImportError(
                        "upload_truncated",
                        "The upload ended before the declared file length.",
                    )
                stage_handle.write(payload)
                written += len(payload)
                remaining -= len(payload)
            stage_handle.flush()
            os.fsync(stage_handle.fileno())

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
                stage_handle=stage_handle,
            )
        finally:
            if stage_handle is not None:
                stage_handle.close()
            self.discard_private_stage(stage_path)

    def allocate_private_stage(self, *, owner_run_id: str | None = None) -> Path:
        """Allocate a private project stage for a supervised ingestion adapter."""

        self._require_anchor()
        return self._allocate_stage(owner_run_id=owner_run_id)

    def open_private_stage(self, stage_path: Path, *, writable: bool) -> BinaryIO:
        """Pin one private staging file so writers cannot follow path replacement."""

        anchor = self._required_anchor()
        relative = anchor.relative(stage_path)
        if (
            relative.parent != anchor.relative(self.tmp_root)
            or not relative.name.startswith(".upload-")
            or not relative.name.endswith(".tmp")
        ):
            raise MediaImportError(
                "unsafe_storage_path",
                "The staged media file is outside project-private storage.",
                retryable=False,
            )
        descriptor = -1
        try:
            with anchor.parent(stage_path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    (os.O_RDWR if writable else os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise _integrity_error()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _integrity_error() from None
            handle = os.fdopen(descriptor, "r+b" if writable else "rb")
            descriptor = -1
            return handle
        except MediaImportError:
            raise
        except OSError:
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def discard_private_stage(self, stage_path: Path) -> None:
        """Remove only the anchored staging name; never follow a replaced ancestor."""

        self._unlink_private_file(stage_path)

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
        stage_handle: BinaryIO | None = None,
    ) -> tuple[SourceReference, MediaAsset]:
        """Validate and atomically publish a complete private PCM16-WAV stage."""

        self._require_anchor()
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
        owns_handle = stage_handle is None
        handle = stage_handle or self.open_private_stage(stage_path, writable=False)
        try:
            return self._publish_staged_handle(
                stage_path,
                handle,
                byte_length=byte_length,
                display_name=display_name,
                source_id=source_id,
                locator=locator,
                expected_asset_id=expected_asset_id,
            )
        finally:
            if owns_handle:
                handle.close()

    def _publish_staged_handle(
        self,
        stage_path: Path,
        handle: BinaryIO,
        *,
        byte_length: int,
        display_name: str,
        source_id: str,
        locator: LocalLocator | RemoteLocator,
        expected_asset_id: str | None,
    ) -> tuple[SourceReference, MediaAsset]:
        stage_identity = self._require_stage_handle_identity(
            stage_path,
            handle,
            byte_length,
        )
        digest = hashlib.sha256()
        handle.seek(0)
        for payload in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(payload)
        self._require_stage_handle_identity(stage_path, handle, byte_length)
        hex_digest = digest.hexdigest()
        asset_id = f"sha256:{hex_digest}"
        if expected_asset_id is not None and asset_id != expected_asset_id:
            raise MediaImportError(
                "stage_identity_changed",
                "The private acquisition stage changed during validation.",
                retryable=False,
            )
        info = inspect_pcm16_wav_stream(handle, file_size=byte_length)
        self._require_stage_handle_identity(stage_path, handle, byte_length)
        bucket_frames = waveform_bucket_frames(info.duration_frames)
        waveform = build_waveform_stream(handle, info, bucket_frames=bucket_frames)
        self._require_stage_handle_identity(stage_path, handle, byte_length)

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
                    self._require_stage_handle_identity(
                        stage_path,
                        handle,
                        byte_length,
                    )
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

    def _require_stage_handle_identity(
        self,
        stage_path: Path,
        handle: BinaryIO,
        byte_length: int,
    ) -> tuple[int, int, int, int]:
        anchor = self._required_anchor()
        entry = os.fstat(handle.fileno())
        try:
            with anchor.parent(stage_path) as (parent_fd, leaf):
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            raise MediaImportError(
                "stage_identity_changed",
                "The private acquisition stage changed during validation.",
                retryable=False,
            ) from None
        identity = (
            entry.st_dev,
            entry.st_ino,
            entry.st_mtime_ns,
            entry.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.getuid()
            or stat.S_IMODE(entry.st_mode) != 0o600
            or entry.st_nlink != 1
            or entry.st_size != byte_length
            or identity
            != (
                named.st_dev,
                named.st_ino,
                named.st_mtime_ns,
                named.st_ctime_ns,
            )
        ):
            raise MediaImportError(
                "stage_identity_changed",
                "The private acquisition stage changed during validation.",
                retryable=False,
            )
        return identity

    def list_public_sources(self) -> list[dict[str, Any]]:
        self._require_anchor()
        values = []
        try:
            anchor = self._required_anchor()
            with anchor.directory(anchor.relative(self.sources_root)) as descriptor:
                names = tuple(sorted(os.listdir(descriptor)))
        except OSError:
            raise _integrity_error() from None
        for name in names:
            if not name.startswith("src_") or not name.endswith(".json"):
                raise _integrity_error()
            path = self.sources_root / name
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
        self._require_anchor()
        _validate_source_id(source_id)
        path = self.sources_root / f"{source_id}.json"
        try:
            return SourceReference.from_record_mapping(self._read_json(path))
        except MediaImportError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def asset_for_source(self, source_id: str) -> MediaAsset:
        self._require_anchor()
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
        self._require_anchor()
        asset = self.asset_for_source(source_id)
        return self._blob_path(asset.sha256)

    def open_audio_for_source(
        self,
        source_id: str,
        *,
        expected_asset_id: str | None = None,
    ) -> tuple[MediaAsset, BinaryIO]:
        """Return one verified descriptor that remains authoritative until closed."""

        self._require_anchor()
        asset = self.asset_for_source(source_id)
        if expected_asset_id is not None and asset.id != expected_asset_id:
            raise _integrity_error()
        path = self._blob_path(asset.sha256)
        anchor = self._required_anchor()
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise _integrity_error() from None
        try:
            self._verify_blob_descriptor(
                descriptor,
                named,
                asset.sha256,
                asset.byte_length,
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            return asset, os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise

    def waveform_for_source(self, source_id: str) -> Waveform:
        self._require_anchor()
        asset = self.asset_for_source(source_id)
        bucket_frames = waveform_bucket_frames(asset.duration_frames)
        path = self._waveform_path(asset.sha256, bucket_frames=bucket_frames)
        if not self._path_exists(path):
            opened_asset, handle = self.open_audio_for_source(
                source_id,
                expected_asset_id=asset.id,
            )
            with handle:
                info = inspect_pcm16_wav_stream(
                    handle,
                    file_size=opened_asset.byte_length,
                )
                waveform = build_waveform_stream(
                    handle,
                    info,
                    bucket_frames=bucket_frames,
                )
            self._publish_waveform(path, waveform)
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
        self._require_anchor()
        asset = self.asset_for_source(source_id)
        path = self._waveform_path(
            asset.sha256,
            bucket_frames=waveform_bucket_frames(asset.duration_frames),
        )
        return self._unlink_private_file(path)

    def forget_remote_locator(self, source_id: str) -> bool:
        """Remove a Stage 5 URL without deleting its source or immutable media."""

        self._require_anchor()
        _validate_source_id(source_id)
        path = self.locators_root / f"{source_id}.json"
        try:
            value = self._read_json(path)
        except MediaImportError:
            if not self._anchored_exists(path):
                return False
            raise
        if value.get("source_id") != source_id or value.get("channel") != "direct_https":
            raise _integrity_error()
        if not self._unlink_private_file(path):
            return False
        return True

    def _allocate_stage(self, *, owner_run_id: str | None = None) -> Path:
        anchor = self._required_anchor()
        if owner_run_id is not None and (
            len(owner_run_id) != 36
            or not owner_run_id.startswith("acq_")
            or any(
                character not in "0123456789abcdef"
                for character in owner_run_id[4:]
            )
        ):
            raise _integrity_error()
        with anchor.directory(anchor.relative(self.tmp_root)) as parent_fd:
            for _ in range(16):
                owner = "" if owner_run_id is None else f"{owner_run_id}-"
                leaf = f".upload-{owner}{secrets.token_hex(8)}.tmp"
                try:
                    descriptor = os.open(
                        leaf,
                        os.O_CREAT | os.O_EXCL | os.O_RDWR,
                        0o600,
                        dir_fd=parent_fd,
                    )
                except FileExistsError:
                    continue
                try:
                    os.fchmod(descriptor, 0o600)
                except BaseException:
                    os.close(descriptor)
                    descriptor = -1
                    os.unlink(leaf, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                    raise
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                return self.tmp_root / leaf
        raise MediaImportError(
            "storage_integrity",
            "Project media storage failed an integrity check.",
            retryable=False,
        )

    def _ensure_storage_ignore(self) -> None:
        path = self.root / ".gitignore"
        expected = "*\n!.gitignore\n"
        if self._path_exists(path):
            if self._read_text(path) != expected:
                raise _integrity_error()
            return
        self._publish_json_text_exclusive(path, expected)

    def _publish_json_text_exclusive(self, path: Path, payload: str) -> None:
        try:
            create_text_exclusive(
                path,
                payload,
                stage_prefix=".record-",
                mode=0o600,
                sync_directory=True,
                anchor=self._required_anchor(),
            )
        except TargetOccupiedError:
            if self._read_text(path) != payload:
                raise _integrity_error() from None
        except OSError:
            raise _integrity_error() from None

    def _blob_path(self, digest: str, *, create_parent: bool = False) -> Path:
        _validate_digest(digest)
        parent = self.blobs_root / digest[:2]
        self._require_private_parent(parent, create=create_parent)
        return parent / f"{digest}.wav"

    def _manifest_path(self, digest: str, *, create_parent: bool = False) -> Path:
        _validate_digest(digest)
        parent = self.manifests_root / digest[:2]
        self._require_private_parent(parent, create=create_parent)
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
        self._require_private_parent(parent, create=create_parent)
        return parent / f"{digest}.pcm16-folded-peak-v1-{bucket_frames}.json"

    def _require_private_parent(self, parent: Path, *, create: bool) -> None:
        try:
            with self._required_anchor().directory(
                self._required_anchor().relative(parent),
                create=create,
            ):
                pass
        except OSError:
            raise _integrity_error() from None

    def _load_existing_asset(
        self,
        manifest_path: Path,
        blob_path: Path,
        digest: str,
    ) -> MediaAsset | None:
        if not self._path_exists(manifest_path):
            return None
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
        anchor = self._required_anchor()
        try:
            with (
                anchor.parent(stage_path) as (stage_fd, stage_leaf),
                anchor.parent(blob_path) as (blob_fd, blob_leaf),
            ):
                os.link(
                    stage_leaf,
                    blob_leaf,
                    src_dir_fd=stage_fd,
                    dst_dir_fd=blob_fd,
                    follow_symlinks=False,
                )
                os.fsync(blob_fd)
        except FileExistsError:
            self._verify_blob(blob_path, digest, byte_length)
            return False
        try:
            with (
                anchor.parent(stage_path) as (stage_fd, stage_leaf),
                anchor.parent(blob_path) as (blob_fd, blob_leaf),
            ):
                blob_entry = os.stat(blob_leaf, dir_fd=blob_fd, follow_symlinks=False)
                if (blob_entry.st_dev, blob_entry.st_ino) != stage_identity[:2]:
                    os.unlink(blob_leaf, dir_fd=blob_fd)
                    os.fsync(blob_fd)
                    raise MediaImportError(
                        "stage_identity_changed",
                        "The private acquisition stage changed during publication.",
                        retryable=False,
                    )
                os.unlink(stage_leaf, dir_fd=stage_fd)
                os.fsync(stage_fd)
        except OSError:
            raise _integrity_error() from None
        try:
            self._verify_blob(blob_path, digest, byte_length)
        except Exception:
            try:
                with anchor.parent(blob_path) as (blob_fd, blob_leaf):
                    os.unlink(blob_leaf, dir_fd=blob_fd)
                    os.fsync(blob_fd)
            except FileNotFoundError:
                pass
            raise
        return True

    def _verify_blob(self, path: Path, digest: str, byte_length: int) -> None:
        anchor = self._required_anchor()
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise _integrity_error() from None
        try:
            self._verify_blob_descriptor(descriptor, named, digest, byte_length)
        finally:
            os.close(descriptor)

    def _anchored_exists(self, path: Path) -> bool:
        anchor = self._required_anchor()
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            raise _integrity_error() from None
        return True

    def _unlink_private_file(self, path: Path) -> bool:
        anchor = self._required_anchor()
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                try:
                    descriptor = os.open(
                        leaf,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                except FileNotFoundError:
                    return False
                entry = os.fstat(descriptor)
                named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) != 0o600
                    or entry.st_nlink != 1
                    or (entry.st_dev, entry.st_ino) != (named.st_dev, named.st_ino)
                ):
                    raise _integrity_error()
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
        except MediaImportError:
            raise
        except OSError:
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return True

    @staticmethod
    def _verify_blob_descriptor(
        descriptor: int,
        named: os.stat_result,
        digest: str,
        byte_length: int,
    ) -> None:
        file_stat = os.fstat(descriptor)
        for _ in range(20):
            if file_stat.st_nlink == 1:
                break
            time.sleep(0.001)
            file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.getuid()
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_nlink != 1
            or file_stat.st_size != byte_length
            or (file_stat.st_dev, file_stat.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise _integrity_error()
        observed = hashlib.sha256()
        while True:
            payload = os.read(descriptor, _CHUNK_BYTES)
            if not payload:
                break
            observed.update(payload)
        after = os.fstat(descriptor)
        if (
            observed.hexdigest() != digest
            or (file_stat.st_dev, file_stat.st_ino, file_stat.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            raise _integrity_error()

    def _publish_waveform(self, path: Path, waveform: Waveform) -> None:
        value = waveform.to_mapping()
        if self._path_exists(path):
            if self._read_json(path) != value:
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
        try:
            create_text_exclusive(
                path,
                payload,
                stage_prefix=".record-",
                mode=0o600,
                sync_directory=True,
                anchor=self._required_anchor(),
            )
        except TargetOccupiedError:
            if self._read_json(path) != value:
                raise MediaImportError(
                    collision_code,
                    "Project media storage contains a conflicting immutable record.",
                    retryable=False,
                ) from None
        except OSError:
            raise _integrity_error() from None

    def _read_json(self, path: Path) -> dict[str, Any]:
        value = None
        for attempt in range(20):
            try:
                anchor = self._required_anchor()
                with anchor.parent(path) as (parent_fd, leaf):
                    descriptor = os.open(
                        leaf,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
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
                payload = self._read_stable_descriptor(descriptor, entry)
                value = json.loads(
                    payload,
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_constant,
                )
                break
            except (OSError, UnicodeError, ValueError):
                raise _integrity_error() from None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        if not isinstance(value, dict):
            raise _integrity_error()
        return value

    def _read_stable_descriptor(
        self,
        descriptor: int,
        before: os.stat_result,
    ) -> bytes:
        payload = bytearray()
        while len(payload) <= _MAX_RECORD_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _MAX_RECORD_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) > _MAX_RECORD_BYTES
            or len(payload) != before.st_size
            or (after.st_dev, after.st_ino, after.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
        ):
            raise _integrity_error()
        return bytes(payload)

    def _read_text(self, path: Path) -> str:
        anchor = self._required_anchor()
        descriptor = -1
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or entry.st_size > _MAX_RECORD_BYTES
            ):
                raise _integrity_error()
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                value = handle.read(_MAX_RECORD_BYTES + 1)
        except (OSError, UnicodeError):
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(value.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise _integrity_error()
        return value

    def _required_anchor(self) -> ProjectAnchor:
        if self._anchor is None:
            raise _integrity_error()
        return self._anchor

    def _path_exists(self, path: Path) -> bool:
        try:
            with self._required_anchor().parent(path) as (parent_fd, leaf):
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            raise _integrity_error() from None

    def _require_anchor(self) -> None:
        try:
            anchor = self._required_anchor()
            anchor.verify()
            for directory in (
                self.media_root,
                self.blobs_base,
                self.blobs_root,
                self.manifests_base,
                self.manifests_root,
                self.sources_root,
                self.private_root,
                self.locators_root,
                self.cache_root,
                self.waveforms_root,
                self.tmp_root,
            ):
                with anchor.directory(anchor.relative(directory)):
                    pass
        except OSError:
            raise _integrity_error() from None

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


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


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


def _ensure_private_directory_anchored(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path), create=True):
            pass
    except OSError:
        raise MediaImportError(
            "unsafe_storage_path",
            "Project media storage contains an unsafe path.",
            retryable=False,
        ) from None


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


def _require_private_directory_anchored(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)):
            pass
    except OSError:
        raise MediaImportError(
            "unsafe_storage_path",
            "Project media storage contains an unsafe path.",
            retryable=False,
        ) from None


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
