from __future__ import annotations

from dataclasses import dataclass

from chordatlas.media.models import FrameRange, Timebase


@dataclass(frozen=True)
class PlaybackState:
    position_frame: int
    playing: bool = False
    loop: FrameRange | None = None

    def to_mapping(self) -> dict[str, object]:
        value: dict[str, object] = {
            "position_frame": self.position_frame,
            "playing": self.playing,
        }
        if self.loop is not None:
            value["loop"] = self.loop.to_mapping()
        return value


class PlaybackController:
    """Deterministic frame-domain behavior mirrored by the browser adapter."""

    def __init__(self, timebase: Timebase) -> None:
        self.timebase = timebase
        self._state = PlaybackState(position_frame=0)

    @property
    def state(self) -> PlaybackState:
        return self._state

    def play(self) -> PlaybackState:
        self._state = PlaybackState(
            position_frame=self._state.position_frame,
            playing=True,
            loop=self._state.loop,
        )
        return self._state

    def pause(self) -> PlaybackState:
        self._state = PlaybackState(
            position_frame=self._state.position_frame,
            playing=False,
            loop=self._state.loop,
        )
        return self._state

    def seek(self, frame: int) -> PlaybackState:
        if type(frame) is not int:
            raise TypeError("frame must be an integer")
        self._state = PlaybackState(
            position_frame=self.timebase.clamp_frame(frame),
            playing=self._state.playing,
            loop=self._state.loop,
        )
        return self._state

    def set_loop(self, value: FrameRange | None) -> PlaybackState:
        if value is not None:
            self.timebase.validate_range(value)
        position = self._state.position_frame
        if value is not None and not (value.start_frame <= position < value.end_frame):
            position = value.start_frame
        self._state = PlaybackState(
            position_frame=position,
            playing=self._state.playing,
            loop=value,
        )
        return self._state

    def advance(self, frames: int) -> PlaybackState:
        if type(frames) is not int:
            raise TypeError("frames must be an integer")
        if frames < 0:
            raise ValueError("frames must be non-negative")
        if not self._state.playing or frames == 0:
            return self._state
        target = self._state.position_frame + frames
        loop = self._state.loop
        if loop is not None and target >= loop.end_frame:
            target = loop.start_frame + ((target - loop.start_frame) % loop.length_frames)
        else:
            target = min(target, self.timebase.duration_frames)
        playing = self._state.playing and target < self.timebase.duration_frames
        self._state = PlaybackState(position_frame=target, playing=playing, loop=loop)
        return self._state
