from __future__ import annotations

from dataclasses import dataclass

from chordatlas.media import FrameRange, Timebase
from chordatlas.practice.models import MAX_RATE_MILLI, MIN_RATE_MILLI


@dataclass(frozen=True)
class PracticeRuntimeState:
    position_frame: int
    playing: bool
    loop: FrameRange
    rate_milli: int
    count_in_remaining: int
    completed_loops: int
    repeat: bool


class PracticePlaybackController:
    """Deterministic source-frame behavior mirrored by the browser adapter."""

    def __init__(
        self,
        timebase: Timebase,
        *,
        loop: FrameRange,
        rate_milli: int,
        count_in_beats: int,
        repeat: bool = True,
    ) -> None:
        timebase.validate_range(loop)
        if not MIN_RATE_MILLI <= rate_milli <= MAX_RATE_MILLI:
            raise ValueError("practice rate is outside the supported range")
        if not 0 <= count_in_beats <= 32:
            raise ValueError("count-in beat count is invalid")
        self.timebase = timebase
        self._state = PracticeRuntimeState(
            position_frame=loop.start_frame,
            playing=False,
            loop=loop,
            rate_milli=rate_milli,
            count_in_remaining=count_in_beats,
            completed_loops=0,
            repeat=repeat,
        )

    @property
    def state(self) -> PracticeRuntimeState:
        return self._state

    def start(self) -> PracticeRuntimeState:
        self._state = PracticeRuntimeState(
            position_frame=self._state.position_frame,
            playing=self._state.count_in_remaining == 0,
            loop=self._state.loop,
            rate_milli=self._state.rate_milli,
            count_in_remaining=self._state.count_in_remaining,
            completed_loops=self._state.completed_loops,
            repeat=self._state.repeat,
        )
        return self._state

    def count_in_tick(self) -> PracticeRuntimeState:
        remaining = self._state.count_in_remaining
        if remaining <= 0:
            return self._state
        remaining -= 1
        self._state = PracticeRuntimeState(
            position_frame=self._state.position_frame,
            playing=remaining == 0,
            loop=self._state.loop,
            rate_milli=self._state.rate_milli,
            count_in_remaining=remaining,
            completed_loops=self._state.completed_loops,
            repeat=self._state.repeat,
        )
        return self._state

    def pause(self) -> PracticeRuntimeState:
        self._state = PracticeRuntimeState(
            position_frame=self._state.position_frame,
            playing=False,
            loop=self._state.loop,
            rate_milli=self._state.rate_milli,
            count_in_remaining=0,
            completed_loops=self._state.completed_loops,
            repeat=self._state.repeat,
        )
        return self._state

    def seek(self, frame: int) -> PracticeRuntimeState:
        if not self._state.loop.start_frame <= frame < self._state.loop.end_frame:
            raise ValueError("practice seek must stay inside the selected range")
        self._state = PracticeRuntimeState(
            position_frame=frame,
            playing=self._state.playing,
            loop=self._state.loop,
            rate_milli=self._state.rate_milli,
            count_in_remaining=self._state.count_in_remaining,
            completed_loops=self._state.completed_loops,
            repeat=self._state.repeat,
        )
        return self._state

    def advance_source_frames(self, frames: int) -> PracticeRuntimeState:
        if frames < 0:
            raise ValueError("frames must be non-negative")
        if not self._state.playing or frames == 0:
            return self._state
        offset = self._state.position_frame - self._state.loop.start_frame + frames
        if not self._state.repeat and offset >= self._state.loop.length_frames:
            self._state = PracticeRuntimeState(
                position_frame=self._state.loop.end_frame,
                playing=False,
                loop=self._state.loop,
                rate_milli=self._state.rate_milli,
                count_in_remaining=0,
                completed_loops=self._state.completed_loops,
                repeat=False,
            )
            return self._state
        loops, remainder = divmod(offset, self._state.loop.length_frames)
        self._state = PracticeRuntimeState(
            position_frame=self._state.loop.start_frame + remainder,
            playing=True,
            loop=self._state.loop,
            rate_milli=self._state.rate_milli,
            count_in_remaining=0,
            completed_loops=self._state.completed_loops + loops,
            repeat=self._state.repeat,
        )
        return self._state

    def wall_milliseconds_for_source_frames(self, frames: int) -> int:
        if frames < 0:
            raise ValueError("frames must be non-negative")
        divisor = self.timebase.sample_rate * self._state.rate_milli
        return (frames * 1_000_000 + divisor // 2) // divisor
