"""Lightweight runtime stabilization diagnostics for realtime calls."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class PlaybackToken:
    generation: int
    request_id: str | None = None
    response_id: str | None = None


class RuntimeStabilityGuard:
    """Small, per-call guard for race-prone realtime orchestration edges."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        storm_window_seconds: float = 3.0,
        storm_threshold: int = 3,
    ) -> None:
        self._session_id = session_id
        self._clock = clock
        self._storm_window_seconds = storm_window_seconds
        self._storm_threshold = storm_threshold
        self._interruptions: deque[float] = deque()
        self._active_playback: PlaybackToken | None = None
        self._last_stale_generations: deque[tuple[int, str]] = deque(maxlen=12)

    @property
    def active_playback(self) -> PlaybackToken | None:
        return self._active_playback

    def record_interruption(self, *, source: str, generation: int) -> bool:
        now = self._clock()
        self._interruptions.append(now)
        while self._interruptions and now - self._interruptions[0] > self._storm_window_seconds:
            self._interruptions.popleft()

        storm_detected = len(self._interruptions) >= self._storm_threshold
        if storm_detected:
            log_event(
                logger,
                "state_guard_triggered",
                session_id=self._session_id,
                guard_name="interruption_storm",
                source=source,
                generation=generation,
                interruption_count=len(self._interruptions),
                window_seconds=self._storm_window_seconds,
            )
        return storm_detected

    def start_playback(
        self,
        *,
        generation: int,
        current_generation: int,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> bool:
        if generation != current_generation:
            log_event(
                logger,
                "stale_playback_blocked",
                session_id=self._session_id,
                generation=generation,
                current_generation=current_generation,
                request_id=request_id,
                response_id=response_id,
            )
            self.record_stale_generation(
                generation=generation,
                current_generation=current_generation,
                source="playback_start",
                request_id=request_id,
                response_id=response_id,
            )
            return False

        if self._active_playback is not None:
            log_event(
                logger,
                "state_guard_triggered",
                session_id=self._session_id,
                guard_name="duplicate_playback_start",
                active_generation=self._active_playback.generation,
                generation=generation,
                request_id=request_id,
                response_id=response_id,
            )
            log_event(
                logger,
                "stale_playback_blocked",
                session_id=self._session_id,
                generation=generation,
                current_generation=current_generation,
                request_id=request_id,
                response_id=response_id,
            )
            return False

        self._active_playback = PlaybackToken(
            generation=generation,
            request_id=request_id,
            response_id=response_id,
        )
        return True

    def finish_playback(
        self,
        *,
        generation: int | None = None,
        reason: str = "finished",
        cancelled: bool = False,
        queue_frames_flushed: int = 0,
    ) -> None:
        active = self._active_playback
        if active is not None and (generation is None or active.generation == generation):
            self._active_playback = None

        log_event(
            logger,
            "playback_cleanup_completed",
            session_id=self._session_id,
            generation=generation if generation is not None else active.generation if active else None,
            reason=reason,
            cancelled=cancelled,
            queue_frames_flushed=queue_frames_flushed,
        )

    def record_stale_generation(
        self,
        *,
        generation: int,
        current_generation: int,
        source: str,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        key = (generation, source)
        if key in self._last_stale_generations:
            return
        self._last_stale_generations.append(key)
        log_event(
            logger,
            "stale_generation_cancelled",
            session_id=self._session_id,
            generation=generation,
            current_generation=current_generation,
            source=source,
            request_id=request_id,
            response_id=response_id,
        )
