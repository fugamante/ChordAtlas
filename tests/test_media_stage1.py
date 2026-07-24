from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
import struct
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import chordatlas.media.store as media_store_module

from chordatlas.media import (
    FrameRange,
    MediaImportError,
    PlaybackController,
    ProjectMediaStore,
    Timebase,
)
from chordatlas.media.wav import MAX_WAVEFORM_BUCKETS, waveform_bucket_frames


def synthetic_wav(
    *,
    frames: int = 2050,
    sample_rate: int = 8_000,
    channels: int = 1,
    sample_width: int = 2,
    phase: float = 0.0,
) -> bytes:
    """Generate original synthetic audio; no third-party recording is embedded."""

    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        if sample_width == 2:
            values = []
            for frame in range(frames):
                sample = round(math.sin((frame / 32) + phase) * 20_000)
                values.extend([sample] * channels)
            writer.writeframes(struct.pack(f"<{len(values)}h", *values))
        else:
            writer.writeframes(bytes((frame % 255 for frame in range(frames * channels))))
    return output.getvalue()


def import_wav(
    store: ProjectMediaStore,
    payload: bytes,
    *,
    name: str = "synthetic.wav",
):
    return store.import_stream(
        io.BytesIO(payload),
        byte_length=len(payload),
        display_name=name,
        authorization_confirmed=True,
    )


def test_import_is_content_addressed_private_and_waveform_complete(tmp_path: Path) -> None:
    payload = synthetic_wav()
    digest = hashlib.sha256(payload).hexdigest()
    store = ProjectMediaStore.initialize(tmp_path)

    source, asset = import_wav(store, payload, name="/private/session/synthetic.wav")
    waveform = store.waveform_for_source(source.id)

    assert asset.id == f"sha256:{digest}"
    assert asset.duration_frames == 2050
    assert asset.sample_rate == 8_000
    assert source.display_name == "synthetic.wav"
    assert source.authorization_basis == "user_attested_authorized"
    assert waveform.buckets[0].start_frame == 0
    assert waveform.buckets[-1].end_frame == asset.duration_frames
    assert len(waveform.buckets) == 3
    assert waveform.buckets[-1].end_frame - waveform.buckets[-1].start_frame == 2

    public = json.dumps(store.list_public_sources(), sort_keys=True)
    assert digest not in public
    assert str(tmp_path) not in public
    assert "private/session" not in public
    assert "RIFF" not in public

    blob = store.audio_path_for_source(source.id)
    assert blob.read_bytes() == payload
    assert stat.S_IMODE(blob.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700


def test_duplicate_bytes_create_distinct_sources_for_one_asset(tmp_path: Path) -> None:
    payload = synthetic_wav()
    store = ProjectMediaStore.initialize(tmp_path)

    first_source, first_asset = import_wav(store, payload, name="first.wav")
    second_source, second_asset = import_wav(store, payload, name="second.wav")

    assert first_source.id != second_source.id
    assert first_source.asset_id == second_source.asset_id
    assert first_asset == second_asset
    assert len(store.list_public_sources()) == 2
    assert len(list(store.blobs_root.rglob("*.wav"))) == 1
    assert len(list(store.manifests_root.rglob("*.json"))) == 1


def test_concurrent_stores_converge_on_one_deterministic_asset(tmp_path: Path) -> None:
    payload = synthetic_wav()
    first = ProjectMediaStore.initialize(tmp_path)
    second = ProjectMediaStore.open(tmp_path)

    def run(store: ProjectMediaStore):
        return import_wav(store, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, (first, second)))

    assert results[0][1] == results[1][1]
    assert len(list(first.blobs_root.rglob("*.wav"))) == 1
    assert len(list(first.manifests_root.rglob("*.json"))) == 1
    assert len(list(first.sources_root.glob("src_*.json"))) == 2


def test_changed_bytes_under_same_name_create_new_asset(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)

    _source_a, asset_a = import_wav(store, synthetic_wav(phase=0.0), name="take.wav")
    _source_b, asset_b = import_wav(store, synthetic_wav(phase=0.5), name="take.wav")

    assert asset_a.id != asset_b.id
    assert len(list(store.blobs_root.rglob("*.wav"))) == 2


def test_verified_audio_handle_cannot_be_retargeted_after_validation(
    tmp_path: Path,
) -> None:
    original = synthetic_wav(phase=0.0)
    replacement = synthetic_wav(phase=0.5)
    assert len(original) == len(replacement)
    store = ProjectMediaStore.initialize(tmp_path)
    source, asset = import_wav(store, original)

    opened_asset, handle = store.open_audio_for_source(
        source.id,
        expected_asset_id=asset.id,
    )
    blob = store.audio_path_for_source(source.id)
    displaced = blob.with_suffix(".verified")
    blob.rename(displaced)
    blob.write_bytes(replacement)
    os.chmod(blob, 0o600)

    with handle:
        assert opened_asset == asset
        assert handle.read() == original
    with pytest.raises(MediaImportError) as changed:
        store.open_audio_for_source(source.id, expected_asset_id=asset.id)
    assert changed.value.code == "storage_integrity"


@pytest.mark.parametrize("operation", ("open", "verify"))
def test_blob_descriptor_closes_when_named_identity_check_fails(
    tmp_path: Path,
    monkeypatch,
    operation: str,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source, asset = import_wav(store, synthetic_wav())
    blob = store.audio_path_for_source(source.id)
    monkeypatch.setattr(store, "asset_for_source", lambda _source_id: asset)
    opened: list[int] = []
    original_open = os.open
    original_stat = os.stat

    def tracked_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if str(path).endswith(".wav") and kwargs.get("dir_fd") is not None:
            opened.append(descriptor)
        return descriptor

    def missing_named_blob(path, *args, **kwargs):
        if str(path).endswith(".wav") and kwargs.get("dir_fd") is not None:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "stat", missing_named_blob)

    with pytest.raises(MediaImportError) as failed:
        if operation == "open":
            store.open_audio_for_source(source.id, expected_asset_id=asset.id)
        else:
            store._verify_blob(blob, asset.sha256, asset.byte_length)

    assert failed.value.code == "storage_integrity"
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_replaced_storage_ancestor_fails_before_external_publication(
    tmp_path: Path,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    original_sources = tmp_path / "original-sources"
    store.sources_root.rename(original_sources)
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    store.sources_root.symlink_to(external, target_is_directory=True)

    with pytest.raises(MediaImportError) as changed:
        import_wav(store, synthetic_wav())

    assert changed.value.code == "storage_integrity"
    assert list(external.iterdir()) == []
    assert list(store.tmp_root.iterdir()) == []


def test_import_never_writes_through_replaced_stage_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    original_allocate = store._allocate_stage
    external = tmp_path / "external-stage"
    external.mkdir(mode=0o700)

    def allocate_then_replace() -> Path:
        stage = original_allocate()
        store.tmp_root.rename(tmp_path / "original-stage")
        store.tmp_root.symlink_to(external, target_is_directory=True)
        return stage

    monkeypatch.setattr(store, "_allocate_stage", allocate_then_replace)
    payload = synthetic_wav()

    with pytest.raises(MediaImportError) as rejected:
        store.import_stream(
            io.BytesIO(payload),
            byte_length=len(payload),
            display_name="fixture.wav",
            authorization_confirmed=True,
        )

    assert rejected.value.code == "storage_integrity"
    assert list(external.iterdir()) == []


def test_import_open_failure_cleans_private_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)

    def fail_open(_stage_path: Path, *, writable: bool):
        assert writable
        raise MediaImportError(
            "storage_integrity",
            "Project media storage failed an integrity check.",
            retryable=False,
        )

    monkeypatch.setattr(store, "open_private_stage", fail_open)
    payload = synthetic_wav()

    with pytest.raises(MediaImportError):
        store.import_stream(
            io.BytesIO(payload),
            byte_length=len(payload),
            display_name="fixture.wav",
            authorization_confirmed=True,
        )

    assert list(store.tmp_root.iterdir()) == []


def test_authorization_and_size_are_checked_before_staging(tmp_path: Path) -> None:
    payload = synthetic_wav()
    store = ProjectMediaStore.initialize(tmp_path)

    with pytest.raises(MediaImportError, match="authorized") as denied:
        store.import_stream(
            io.BytesIO(payload),
            byte_length=len(payload),
            display_name="synthetic.wav",
            authorization_confirmed=False,
        )
    assert denied.value.code == "authorization_required"

    with pytest.raises(MediaImportError, match="exceeds") as oversized:
        store.import_stream(
            io.BytesIO(payload),
            byte_length=len(payload),
            display_name="synthetic.wav",
            authorization_confirmed=True,
            max_upload_bytes=len(payload) - 1,
        )
    assert oversized.value.code == "upload_too_large"
    assert list(store.tmp_root.iterdir()) == []


def test_invalid_and_truncated_wav_leave_no_valid_asset(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)

    with pytest.raises(MediaImportError) as invalid:
        import_wav(store, b"not a wave file")
    assert invalid.value.code == "unsupported_container"

    payload = synthetic_wav()
    with pytest.raises(MediaImportError) as truncated:
        store.import_stream(
            io.BytesIO(payload[:-5]),
            byte_length=len(payload),
            display_name="truncated.wav",
            authorization_confirmed=True,
        )
    assert truncated.value.code == "upload_truncated"
    assert list(store.sources_root.iterdir()) == []
    assert list(store.manifests_root.rglob("*.json")) == []
    assert list(store.tmp_root.iterdir()) == []


def test_decoder_rejects_non_pcm16_without_publishing(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    payload = synthetic_wav(sample_width=1)

    with pytest.raises(MediaImportError) as rejected:
        import_wav(store, payload)

    assert rejected.value.code == "unsupported_sample_width"
    assert list(store.sources_root.iterdir()) == []


def test_decoder_accepts_stereo_pcm16(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source, asset = import_wav(store, synthetic_wav(channels=2))

    assert asset.channels == 2
    assert store.waveform_for_source(source.id).timebase == asset.timebase


def test_fixture_manifest_is_authorized_and_contains_no_recordings() -> None:
    path = Path(__file__).parent / "fixtures" / "audio" / "fixture-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert len(manifest["fixtures"]) >= 4
    assert all(item["generated"] and item["license"] == "MIT" for item in manifest["fixtures"])
    assert manifest["review"] == {
        "contains_copyrighted_lyrics": False,
        "contains_third_party_recording": False,
        "redistribution_authorized": True,
    }


def test_existing_blob_mismatch_fails_closed_without_overwrite(tmp_path: Path) -> None:
    payload = synthetic_wav()
    digest = hashlib.sha256(payload).hexdigest()
    store = ProjectMediaStore.initialize(tmp_path)
    parent = store.blobs_root / digest[:2]
    parent.mkdir(mode=0o700)
    blob = parent / f"{digest}.wav"
    blob.write_bytes(b"conflict")
    os.chmod(blob, 0o600)

    with pytest.raises(MediaImportError) as collision:
        import_wav(store, payload)

    assert collision.value.code == "storage_integrity"
    assert blob.read_bytes() == b"conflict"
    assert list(store.sources_root.iterdir()) == []


def test_waveform_cache_is_regenerable_without_removing_source(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    payload = synthetic_wav()
    source, _asset = import_wav(store, payload)

    assert store.prune_waveform_cache(source.id)
    assert not store.prune_waveform_cache(source.id)
    regenerated = store.waveform_for_source(source.id)

    assert regenerated.timebase.duration_frames == 2050
    assert store.audio_path_for_source(source.id).read_bytes() == payload
    assert len(store.list_public_sources()) == 1


def test_waveform_prune_cannot_escape_replaced_cache_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source, _asset = import_wav(store, synthetic_wav())
    waveform = next(store.waveforms_root.rglob("*.json"))
    relative = waveform.relative_to(store.waveforms_root)
    original_asset_for_source = store.asset_for_source
    external = tmp_path / "external-waveforms"
    outside = external / relative
    outside.parent.mkdir(parents=True, mode=0o700)
    outside.write_text("outside\n", encoding="utf-8")
    os.chmod(outside, 0o600)

    def replace_after_lookup(source_id: str):
        asset = original_asset_for_source(source_id)
        store.waveforms_root.rename(tmp_path / "original-waveforms")
        store.waveforms_root.symlink_to(external, target_is_directory=True)
        return asset

    monkeypatch.setattr(store, "asset_for_source", replace_after_lookup)

    with pytest.raises(MediaImportError) as rejected:
        store.prune_waveform_cache(source.id)

    assert rejected.value.code == "storage_integrity"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_locator_forget_cannot_escape_replaced_private_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source, _asset = import_wav(store, synthetic_wav())
    locator = store.locators_root / f"{source.id}.json"
    locator.write_text(
        json.dumps(
            {
                "source_id": source.id,
                "channel": "direct_https",
                "private_locator": "https://audio.example/take.wav",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(locator, 0o600)
    original_read = store._read_json
    external = tmp_path / "external-locators"
    external.mkdir(mode=0o700)
    outside = external / locator.name
    outside.write_bytes(locator.read_bytes())
    os.chmod(outside, 0o600)

    def read_then_replace(path: Path):
        value = original_read(path)
        store.locators_root.rename(tmp_path / "original-locators")
        store.locators_root.symlink_to(external, target_is_directory=True)
        return value

    monkeypatch.setattr(store, "_read_json", read_then_replace)

    with pytest.raises(MediaImportError) as rejected:
        store.forget_remote_locator(source.id)

    assert rejected.value.code == "storage_integrity"
    assert outside.exists()


def test_waveform_cache_cannot_change_asset_timebase(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source, _asset = import_wav(store, synthetic_wav())
    waveform_path = next(store.waveforms_root.rglob("*.json"))
    value = json.loads(waveform_path.read_text(encoding="utf-8"))
    value["timebase"]["sample_rate"] = 9_000
    waveform_path.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(waveform_path, 0o600)

    with pytest.raises(MediaImportError) as corrupt:
        store.waveform_for_source(source.id)

    assert corrupt.value.code == "storage_integrity"


def test_project_storage_rejects_symlink_root(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / ".chordatlas").symlink_to(external, target_is_directory=True)

    with pytest.raises(MediaImportError) as unsafe:
        ProjectMediaStore.initialize(tmp_path)

    assert unsafe.value.code == "unsafe_storage_path"
    assert list(external.iterdir()) == []


def test_project_storage_rejects_permissive_existing_root(tmp_path: Path) -> None:
    media_root = tmp_path / ".chordatlas"
    media_root.mkdir(mode=0o777)
    os.chmod(media_root, 0o777)

    with pytest.raises(MediaImportError) as unsafe:
        ProjectMediaStore.initialize(tmp_path)

    assert unsafe.value.code == "unsafe_storage_permissions"


def test_initialize_rejects_intermediate_project_ancestor_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = tmp_path / "selected"
    project = container / "project"
    project.mkdir(parents=True)
    displaced = tmp_path / "selected-original"
    original = media_store_module._ensure_private_directory_anchored
    replaced = False

    def replace_then_create(path: Path, anchor) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            container.rename(displaced)
            project.mkdir(parents=True)
        original(path, anchor)

    monkeypatch.setattr(
        media_store_module,
        "_ensure_private_directory_anchored",
        replace_then_create,
    )
    with pytest.raises(MediaImportError) as rejected:
        ProjectMediaStore.initialize(project)

    assert rejected.value.code == "unsafe_storage_path"
    assert not (project / ".chordatlas").exists()


def test_project_storage_local_ignore_hides_media_from_git(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    store = ProjectMediaStore.initialize(tmp_path)
    import_wav(store, synthetic_wav())

    result = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "?? .chordatlas/.gitignore"


def test_project_storage_rejects_permissive_ignore_file(tmp_path: Path) -> None:
    ProjectMediaStore.initialize(tmp_path)
    os.chmod(tmp_path / ".chordatlas" / ".gitignore", 0o644)

    with pytest.raises(MediaImportError) as unsafe:
        ProjectMediaStore.open(tmp_path)

    assert unsafe.value.code == "storage_integrity"


def test_waveform_resolution_is_bounded_for_maximum_duration() -> None:
    duration_frames = 192_000 * 2 * 60 * 60
    bucket_frames = waveform_bucket_frames(duration_frames)

    assert math.ceil(duration_frames / bucket_frames) <= MAX_WAVEFORM_BUCKETS


def test_malformed_record_returns_stable_integrity_error(tmp_path: Path) -> None:
    store = ProjectMediaStore.initialize(tmp_path)
    source_id = "src_" + ("a" * 32)
    record = store.sources_root / f"{source_id}.json"
    record.write_text("{}\n", encoding="utf-8")
    os.chmod(record, 0o600)

    with pytest.raises(MediaImportError) as corrupt:
        store.source(source_id)

    assert corrupt.value.code == "storage_integrity"


def test_timebase_and_playback_share_half_open_integer_frames() -> None:
    timebase = Timebase(sample_rate=48_000, duration_frames=480_000)
    controller = PlaybackController(timebase)

    assert timebase.seconds_to_frame("0.0005") == 24
    assert timebase.frame_to_seconds(24) == 0.0005
    assert controller.seek(50_000).position_frame == 50_000
    assert controller.set_loop(FrameRange(48_000, 96_000)).position_frame == 50_000
    assert controller.play().playing
    assert controller.advance(45_999).position_frame == 95_999
    assert controller.advance(2).position_frame == 48_001
    assert controller.pause().position_frame == 48_001
    assert controller.advance(1_000).position_frame == 48_001


def test_playback_clamps_seek_and_stops_at_duration() -> None:
    controller = PlaybackController(Timebase(sample_rate=10, duration_frames=100))

    assert controller.seek(-5).position_frame == 0
    assert controller.seek(500).position_frame == 100
    controller.seek(90)
    controller.play()
    state = controller.advance(20)

    assert state.position_frame == 100
    assert not state.playing
