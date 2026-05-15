from __future__ import annotations

import asyncio
import math
import sys
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voice_agent.audio import PcmFrame
from voice_agent.conversation.diagnostics import RuntimeStabilityGuard
from voice_agent.conversation.orchestrator import ConversationOrchestrator
from voice_agent.conversation.states import ConversationState, PendingAction
from voice_agent.logging_config import get_logger, log_error, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class ActiveAudioWork:
    generation: int
    request_id: str | None
    response_id: str | None


QueueFlushTarget = asyncio.Queue[Any | None]
PlaybackStopCallback = Callable[[], int | None]


class RealtimeInterruptionManager:
    def __init__(
        self,
        *,
        speech_queue: QueueFlushTarget | None = None,
        playback_queue: QueueFlushTarget | None = None,
        speech_rms_threshold: float = 500.0,
        consecutive_speech_frames: int = 1,
        clock: Callable[[], float] = time.perf_counter,
        session_id: str | None = None,
    ) -> None:
        if speech_rms_threshold <= 0:
            raise ValueError("speech_rms_threshold must be positive")
        if consecutive_speech_frames < 1:
            raise ValueError("consecutive_speech_frames must be >= 1")

        self._speech_queue = speech_queue
        self._playback_queue = playback_queue
        self._speech_rms_threshold = speech_rms_threshold
        self._consecutive_speech_frames = consecutive_speech_frames
        self._clock = clock

        self._conversation = ConversationOrchestrator(
            initial_state=ConversationState.LISTENING,
            pending_action=PendingAction.WAIT_FOR_CALLER,
            session_id=session_id,
        )
        self._stability_guard = RuntimeStabilityGuard(session_id=session_id, clock=clock)
        self._generation = 0
        self._speech_frames_seen = 0
        self._silence_frames_seen = 0
        self._playback_stop_callback: PlaybackStopCallback | None = None
        self._active_response_task: asyncio.Task[Any] | None = None
        self._active_response_work: ActiveAudioWork | None = None
        self._active_tts_task: asyncio.Task[Any] | None = None
        self._active_tts_work: ActiveAudioWork | None = None
        self._active_playback_work: ActiveAudioWork | None = None
        self._interrupted_generations: set[int] = set()

    @property
    def state(self) -> ConversationState:
        return self._conversation.current_state

    @property
    def conversation(self) -> ConversationOrchestrator:
        return self._conversation

    @property
    def current_generation(self) -> int:
        return self._generation

    def set_playback_stop_callback(self, callback: PlaybackStopCallback) -> None:
        self._playback_stop_callback = callback

    def observe_caller_audio(self, frame: PcmFrame) -> None:
        rms = _pcm16_rms(frame.data)
        if rms < self._speech_rms_threshold:
            self._speech_frames_seen = 0
            if self._conversation.snapshot().caller_speaking:
                self._silence_frames_seen += 1
                if self._silence_frames_seen >= 2:
                    self._silence_frames_seen = 0
                    self._conversation.mark_caller_stopped(
                        source="inbound_audio",
                        rms=round(rms, 2),
                    )
            return

        self._silence_frames_seen = 0
        self._speech_frames_seen += 1
        if self._speech_frames_seen < self._consecutive_speech_frames:
            return

        self._conversation.mark_caller_speaking(
            source="inbound_audio",
            rms=round(rms, 2),
        )
        if self.state not in {ConversationState.SPEAKING, ConversationState.THINKING}:
            return

        self._speech_frames_seen = 0
        self.request_interruption(source="inbound_audio", rms=round(rms, 2))

    def observe_caller_transcript(
        self,
        *,
        text: str,
        request_id: str | None,
        is_final: bool,
    ) -> None:
        if text.strip():
            self._conversation.mark_caller_speaking(
                request_id=request_id,
                source="stt_transcript",
                transcript_chars=len(text),
                is_final=is_final,
            )
            if is_final:
                self._conversation.mark_caller_stopped(
                    request_id=request_id,
                    source="stt_transcript",
                    transcript_chars=len(text),
                    is_final=is_final,
                )

        if self.state not in {ConversationState.SPEAKING, ConversationState.THINKING}:
            return
        self.request_interruption(
            source="stt_transcript",
            request_id=request_id,
            transcript_chars=len(text),
            is_final=is_final,
        )

    def request_interruption(self, *, source: str, **fields: object) -> bool:
        if self.state not in {ConversationState.SPEAKING, ConversationState.THINKING}:
            log_event(
                logger,
                "state_guard_triggered",
                guard_name="interruption_ignored_non_interruptible_state",
                source=source,
                state=self.state.value,
                current_generation=self._generation,
                **fields,
            )
            return False

        started_at = self._clock()
        interrupted_generation = self._generation
        self._generation += 1
        self._interrupted_generations.add(interrupted_generation)
        self._stability_guard.record_interruption(
            source=source,
            generation=interrupted_generation,
        )

        log_event(
            logger,
            "interruption_detected",
            source=source,
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            state=self.state.value,
            **fields,
        )
        log_event(
            logger,
            "interruption_recovery_started",
            source=source,
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            state=self.state.value,
            **fields,
        )
        if self._active_playback_work is not None:
            self._conversation.mark_ai_speaking_stopped(
                request_id=self._active_playback_work.request_id,
                response_id=self._active_playback_work.response_id,
                reason="interruption_detected",
                interrupted_generation=interrupted_generation,
            )
        self._transition(
            ConversationState.INTERRUPTED,
            reason="caller_speech_interruption",
            pending_action=PendingAction.RECOVER_FROM_INTERRUPTION,
        )

        queued_duration_ms = self._cancel_playback(interrupted_generation)
        self._cancel_active_response(interrupted_generation)
        self._cancel_active_tts(interrupted_generation)
        flushed_items = self._flush_managed_queues(interrupted_generation)
        self._stability_guard.finish_playback(
            generation=interrupted_generation,
            reason="interruption",
            cancelled=True,
            queue_frames_flushed=flushed_items,
        )

        latency_ms = round((self._clock() - started_at) * 1000, 3)
        log_event(
            logger,
            "interruption_latency_timing",
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            latency_ms=latency_ms,
            playback_queued_duration_ms=queued_duration_ms,
        )
        self._active_playback_work = None
        self._conversation.mark_recovered(
            request_id=fields.get("request_id") if isinstance(fields.get("request_id"), str) else None,
            recovery_text=None,
            reason="interruption_handled",
        )
        self._transition(
            ConversationState.LISTENING,
            reason="interruption_handled",
            pending_action=PendingAction.WAIT_FOR_CALLER,
        )
        log_event(
            logger,
            "interruption_recovery_completed",
            source=source,
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            state=self.state.value,
            **fields,
        )
        return True

    def abort_pending_output(self, *, source: str, reason: str, **fields: object) -> int:
        started_at = self._clock()
        interrupted_generation = self._generation
        self._generation += 1
        self._interrupted_generations.add(interrupted_generation)
        self._stability_guard.record_stale_generation(
            generation=interrupted_generation,
            current_generation=self._generation,
            source=source,
            request_id=_string_field(fields, "request_id"),
            response_id=_string_field(fields, "response_id"),
        )

        log_event(
            logger,
            "output_generation_reset",
            source=source,
            reason=reason,
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            state=self.state.value,
            **fields,
        )

        queued_duration_ms = self._cancel_playback(interrupted_generation)
        self._cancel_active_response(interrupted_generation)
        self._cancel_active_tts(interrupted_generation)
        flushed_items = self._flush_managed_queues(interrupted_generation)
        self._stability_guard.finish_playback(
            generation=interrupted_generation,
            reason=reason,
            cancelled=True,
            queue_frames_flushed=flushed_items,
        )
        self._active_playback_work = None

        if self.state != ConversationState.LISTENING:
            self._transition(
                ConversationState.LISTENING,
                reason=reason,
                pending_action=PendingAction.WAIT_FOR_CALLER,
                generation=self._generation,
                source=source,
                **fields,
            )

        latency_ms = round((self._clock() - started_at) * 1000, 3)
        log_event(
            logger,
            "output_generation_reset_latency_timing",
            source=source,
            reason=reason,
            interrupted_generation=interrupted_generation,
            new_generation=self._generation,
            latency_ms=latency_ms,
            playback_queued_duration_ms=queued_duration_ms,
            **fields,
        )
        return self._generation

    def track_response_task(
        self,
        task: asyncio.Task[Any],
        *,
        generation: int,
        request_id: str | None,
    ) -> None:
        if self.is_stale(generation):
            task.cancel()
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source="track_response_task",
                request_id=request_id,
            )
            return
        self._active_response_task = task
        self._active_response_work = ActiveAudioWork(
            generation=generation,
            request_id=request_id,
            response_id=None,
        )

    def clear_response_task(self, task: asyncio.Task[Any]) -> None:
        if self._active_response_task is task:
            self._active_response_task = None
            self._active_response_work = None

    def mark_thinking(self, generation: int, *, request_id: str | None) -> bool:
        if self.is_stale(generation):
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source="mark_thinking",
                request_id=request_id,
            )
            return False
        self._transition(
            ConversationState.THINKING,
            reason="final_transcript_received",
            pending_action=PendingAction.GENERATE_RESPONSE,
            request_id=request_id,
            generation=generation,
        )
        return True

    def mark_speaking(
        self,
        generation: int,
        *,
        request_id: str | None,
        response_id: str | None,
    ) -> bool:
        if self.is_stale(generation):
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source="mark_speaking",
                request_id=request_id,
                response_id=response_id,
            )
            log_event(
                logger,
                "stale_playback_blocked",
                generation=generation,
                current_generation=self._generation,
                request_id=request_id,
                response_id=response_id,
            )
            return False
        if not self._stability_guard.start_playback(
            generation=generation,
            current_generation=self._generation,
            request_id=request_id,
            response_id=response_id,
        ):
            return False
        self._active_playback_work = ActiveAudioWork(
            generation=generation,
            request_id=request_id,
            response_id=response_id,
        )
        if not self._transition(
            ConversationState.SPEAKING,
            reason="playback_started",
            pending_action=PendingAction.PLAY_RESPONSE,
            request_id=request_id,
            response_id=response_id,
            generation=generation,
        ):
            self._active_playback_work = None
            self._stability_guard.finish_playback(
                generation=generation,
                reason="invalid_playback_transition",
                cancelled=True,
            )
            return False
        self._conversation.mark_ai_speaking_started(
            request_id=request_id,
            response_id=response_id,
            generation=generation,
        )
        return True

    def mark_playback_finished(
        self,
        generation: int,
        *,
        request_id: str | None,
        response_id: str | None,
    ) -> bool:
        if self.was_interrupted(generation) or self.is_stale(generation):
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source="playback_finished",
                request_id=request_id,
                response_id=response_id,
            )
            log_event(
                logger,
                "stale_playback_blocked",
                generation=generation,
                current_generation=self._generation,
                request_id=request_id,
                response_id=response_id,
            )
            return False
        self._active_playback_work = None
        self._conversation.mark_ai_speaking_stopped(
            request_id=request_id,
            response_id=response_id,
            reason="playback_completed",
            generation=generation,
        )
        self._transition(
            ConversationState.LISTENING,
            reason="playback_completed",
            pending_action=PendingAction.WAIT_FOR_CALLER,
            request_id=request_id,
            response_id=response_id,
            generation=generation,
        )
        self._stability_guard.finish_playback(
            generation=generation,
            reason="playback_completed",
            cancelled=False,
        )
        return True

    def mark_listening(self, generation: int, *, reason: str, **fields: object) -> bool:
        if self.is_stale(generation):
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source=reason,
                request_id=_string_field(fields, "request_id"),
                response_id=_string_field(fields, "response_id"),
            )
            return False
        active_playback = self._stability_guard.active_playback
        self._active_playback_work = None
        self._transition(
            ConversationState.LISTENING,
            reason=reason,
            pending_action=PendingAction.WAIT_FOR_CALLER,
            generation=generation,
            **fields,
        )
        if active_playback is not None and active_playback.generation == generation:
            self._stability_guard.finish_playback(
                generation=generation,
                reason=reason,
                cancelled=True,
            )
        return True

    def track_tts_task(
        self,
        task: asyncio.Task[Any],
        *,
        generation: int,
        request_id: str | None,
        response_id: str | None,
    ) -> None:
        if self.is_stale(generation):
            task.cancel()
            self._stability_guard.record_stale_generation(
                generation=generation,
                current_generation=self._generation,
                source="track_tts_task",
                request_id=request_id,
                response_id=response_id,
            )
            return
        self._active_tts_task = task
        self._active_tts_work = ActiveAudioWork(
            generation=generation,
            request_id=request_id,
            response_id=response_id,
        )

    def clear_tts_task(self, task: asyncio.Task[Any]) -> None:
        if self._active_tts_task is task:
            self._active_tts_task = None
            self._active_tts_work = None

    def is_stale(self, generation: int) -> bool:
        return generation != self._generation or generation in self._interrupted_generations

    def is_current(self, generation: int) -> bool:
        return not self.is_stale(generation)

    def should_stop_playback(self, generation: int) -> bool:
        return self.is_stale(generation) or self.state == ConversationState.INTERRUPTED

    def was_interrupted(self, generation: int) -> bool:
        return generation in self._interrupted_generations

    def should_emit_ai_response(
        self,
        text: str,
        *,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> bool:
        return self._conversation.should_emit_ai_response(
            text,
            request_id=request_id,
            response_id=response_id,
        )

    def _cancel_playback(self, interrupted_generation: int) -> int | None:
        queued_duration_ms: int | None = None
        callback_configured = self._playback_stop_callback is not None
        if self._playback_stop_callback is not None:
            try:
                queued_duration_ms = self._playback_stop_callback()
            except Exception as exc:
                log_error(
                    logger,
                    "playback_cancel_failed",
                    interrupted_generation=interrupted_generation,
                    error=str(exc),
                )
        log_event(
            logger,
            "playback_cancelled",
            interrupted_generation=interrupted_generation,
            callback_configured=callback_configured,
            queued_duration_ms=queued_duration_ms,
            request_id=self._active_playback_work.request_id if self._active_playback_work else None,
            response_id=self._active_playback_work.response_id if self._active_playback_work else None,
        )
        self._conversation.mark_ai_speaking_stopped(
            request_id=self._active_playback_work.request_id if self._active_playback_work else None,
            response_id=self._active_playback_work.response_id if self._active_playback_work else None,
            reason="playback_cancelled",
            interrupted_generation=interrupted_generation,
        )
        return queued_duration_ms

    def _cancel_active_tts(self, interrupted_generation: int) -> None:
        task = self._active_tts_task
        work = self._active_tts_work
        if task is None or task.done():
            return

        task.cancel()
        if work is not None:
            self._stability_guard.record_stale_generation(
                generation=work.generation,
                current_generation=self._generation,
                source="tts_cancelled",
                request_id=work.request_id,
                response_id=work.response_id,
            )
        log_event(
            logger,
            "tts_cancelled",
            interrupted_generation=interrupted_generation,
            tts_generation=work.generation if work else None,
            request_id=work.request_id if work else None,
            response_id=work.response_id if work else None,
            task_name=task.get_name(),
        )
        self._active_tts_task = None
        self._active_tts_work = None

    def _cancel_active_response(self, interrupted_generation: int) -> None:
        task = self._active_response_task
        work = self._active_response_work
        if task is None or task.done():
            return

        task.cancel()
        if work is not None:
            self._stability_guard.record_stale_generation(
                generation=work.generation,
                current_generation=self._generation,
                source="openai_response_cancelled",
                request_id=work.request_id,
                response_id=work.response_id,
            )
        log_event(
            logger,
            "openai_response_cancelled",
            interrupted_generation=interrupted_generation,
            response_generation=work.generation if work else None,
            request_id=work.request_id if work else None,
            task_name=task.get_name(),
        )
        self._active_response_task = None
        self._active_response_work = None

    def _flush_managed_queues(self, interrupted_generation: int) -> int:
        speech_flushed = self._flush_queue(
            self._speech_queue,
            queue_name="speech_queue",
            interrupted_generation=interrupted_generation,
        )
        playback_flushed = self._flush_queue(
            self._playback_queue,
            queue_name="playback_queue",
            interrupted_generation=interrupted_generation,
        )
        return speech_flushed + playback_flushed

    def _flush_queue(
        self,
        queue: QueueFlushTarget | None,
        *,
        queue_name: str,
        interrupted_generation: int,
    ) -> int:
        if queue is None:
            return 0

        flushed_items = 0
        close_signal_seen = False
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                close_signal_seen = True
            else:
                flushed_items += 1
            queue.task_done()

        if close_signal_seen:
            queue.put_nowait(None)

        log_event(
            logger,
            "queue_flushed",
            queue_name=queue_name,
            interrupted_generation=interrupted_generation,
            flushed_items=flushed_items,
            close_signal_preserved=close_signal_seen,
        )
        return flushed_items

    def _transition(
        self,
        next_state: ConversationState,
        *,
        reason: str,
        pending_action: PendingAction | None = None,
        **fields: object,
    ) -> bool:
        previous_state = self.state
        changed = self._conversation.transition(
            next_state,
            reason=reason,
            pending_action=pending_action,
            current_generation=self._generation,
            **fields,
        )
        if not changed or previous_state == next_state:
            return changed

        log_event(
            logger,
            "conversation_state_transition",
            previous_state=previous_state.value,
            next_state=next_state.value,
            reason=reason,
            current_generation=self._generation,
            **fields,
        )
        return True

    def guard_state_consistency(self, *, reason: str, request_id: str | None = None) -> bool:
        return self._conversation.guard_state_consistency(
            reason=reason,
            request_id=request_id,
        )


def _pcm16_rms(data: bytes) -> float:
    if len(data) < 2:
        return 0.0

    aligned = data[: len(data) - (len(data) % 2)]
    samples = array("h")
    samples.frombytes(aligned)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0

    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _string_field(fields: dict[str, object], key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) else None
