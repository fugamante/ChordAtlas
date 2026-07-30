from __future__ import annotations

import pytest

from chordatlas.media import FrameRange, PlaybackController, Timebase
from chordatlas.practice import PracticePlaybackController


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_playback_rejects_non_integer_frame_inputs(value: object) -> None:
    controller = PlaybackController(Timebase(8_000, 32_000))

    with pytest.raises(TypeError):
        controller.seek(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        controller.advance(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_practice_playback_rejects_non_integer_frame_inputs(value: object) -> None:
    controller = PracticePlaybackController(
        Timebase(8_000, 32_000),
        loop=FrameRange(8_000, 16_000),
        rate_milli=1_000,
        count_in_beats=0,
    )

    with pytest.raises(TypeError):
        controller.seek(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        controller.advance_source_frames(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        controller.wall_milliseconds_for_source_frames(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 1.5, "1000"])
def test_practice_playback_rejects_non_integer_configuration(value: object) -> None:
    with pytest.raises(TypeError):
        PracticePlaybackController(
            Timebase(8_000, 32_000),
            loop=FrameRange(8_000, 16_000),
            rate_milli=value,  # type: ignore[arg-type]
            count_in_beats=0,
        )
    with pytest.raises(TypeError):
        PracticePlaybackController(
            Timebase(8_000, 32_000),
            loop=FrameRange(8_000, 16_000),
            rate_milli=1_000,
            count_in_beats=value,  # type: ignore[arg-type]
        )
