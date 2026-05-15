from __future__ import annotations

import threading
from typing import Any

from voice_agent.conversation.states import (
    BookingStage,
    ConversationSnapshot,
    ConversationState,
    PendingAction,
)
from voice_agent.conversation.transitions import can_transition
from voice_agent.conversation.turn_manager import ResponseRepetitionGuard
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        initial_state: ConversationState = ConversationState.LISTENING,
        booking_stage: BookingStage = BookingStage.IDLE,
        pending_action: PendingAction = PendingAction.WAIT_FOR_CALLER,
        session_id: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._current_state = initial_state
        self._previous_state: ConversationState | None = None
        self._booking_stage = booking_stage
        self._pending_action = pending_action
        self._caller_speaking = False
        self._ai_speaking = False
        self._state_generation = 0
        self._session_id = session_id
        self._repetition_guard = ResponseRepetitionGuard()

    @property
    def current_state(self) -> ConversationState:
        with self._lock:
            return self._current_state

    @property
    def booking_stage(self) -> BookingStage:
        with self._lock:
            return self._booking_stage

    @property
    def pending_action(self) -> PendingAction:
        with self._lock:
            return self._pending_action

    def snapshot(self) -> ConversationSnapshot:
        with self._lock:
            return ConversationSnapshot(
                current_state=self._current_state,
                previous_state=self._previous_state,
                booking_stage=self._booking_stage,
                pending_action=self._pending_action,
                caller_speaking=self._caller_speaking,
                ai_speaking=self._ai_speaking,
                state_generation=self._state_generation,
            )

    def transition(
        self,
        next_state: ConversationState,
        *,
        reason: str,
        pending_action: PendingAction | None = None,
        **fields: Any,
    ) -> bool:
        with self._lock:
            if self._current_state == next_state:
                if pending_action is not None:
                    self._pending_action = pending_action
                return True

            if not can_transition(self._current_state, next_state):
                details = {
                    "session_id": self._session_id,
                    "current_state": self._current_state.value,
                    "attempted_state": next_state.value,
                    "reason": reason,
                    "pending_action": self._pending_action.value,
                    **fields,
                }
                log_event(logger, "invalid_state_transition", **details)
                log_event(logger, "conversation_state_transition_blocked", **details)
                return False

            previous_state = self._current_state
            self._previous_state = previous_state
            self._current_state = next_state
            if pending_action is not None:
                self._pending_action = pending_action
            self._state_generation += 1

            if previous_state == ConversationState.SPEAKING or next_state == ConversationState.INTERRUPTED:
                self._ai_speaking = False

            log_event(
                logger,
                "conversation_state_changed",
                session_id=self._session_id,
                previous_state=previous_state.value,
                current_state=next_state.value,
                reason=reason,
                pending_action=self._pending_action.value,
                booking_stage=self._booking_stage.value,
                state_generation=self._state_generation,
                **fields,
            )
            return True

    def mark_caller_speaking(
        self,
        *,
        request_id: str | None = None,
        source: str,
        **fields: Any,
    ) -> None:
        with self._lock:
            if self._caller_speaking:
                return
            self._caller_speaking = True
            log_event(
                logger,
                "caller_speaking_started",
                session_id=self._session_id,
                request_id=request_id,
                source=source,
                current_state=self._current_state.value,
                **fields,
            )

    def mark_caller_stopped(
        self,
        *,
        request_id: str | None = None,
        source: str,
        **fields: Any,
    ) -> None:
        with self._lock:
            if not self._caller_speaking:
                return
            self._caller_speaking = False
            log_event(
                logger,
                "caller_speaking_stopped",
                session_id=self._session_id,
                request_id=request_id,
                source=source,
                current_state=self._current_state.value,
                **fields,
            )

    def mark_ai_speaking_started(
        self,
        *,
        request_id: str | None,
        response_id: str | None,
        **fields: Any,
    ) -> None:
        with self._lock:
            if self._ai_speaking:
                log_event(
                    logger,
                    "state_guard_triggered",
                    session_id=self._session_id,
                    request_id=request_id,
                    response_id=response_id,
                    guard_name="duplicate_ai_speaking_start",
                    current_state=self._current_state.value,
                    **fields,
                )
            self._ai_speaking = True
            log_event(
                logger,
                "ai_speaking_started",
                session_id=self._session_id,
                request_id=request_id,
                response_id=response_id,
                current_state=self._current_state.value,
                **fields,
            )

    def mark_ai_speaking_stopped(
        self,
        *,
        request_id: str | None,
        response_id: str | None,
        reason: str,
        **fields: Any,
    ) -> None:
        with self._lock:
            if not self._ai_speaking and self._current_state != ConversationState.SPEAKING:
                return
            self._ai_speaking = False
            log_event(
                logger,
                "ai_speaking_stopped",
                session_id=self._session_id,
                request_id=request_id,
                response_id=response_id,
                reason=reason,
                current_state=self._current_state.value,
                **fields,
            )

    def set_booking_stage(
        self,
        stage: BookingStage,
        *,
        reason: str,
        request_id: str | None = None,
        pending_fields: tuple[str, ...] = (),
    ) -> bool:
        with self._lock:
            if stage == self._booking_stage:
                return False
            previous_stage = self._booking_stage
            self._booking_stage = stage
            if stage in {
                BookingStage.CUSTOMER_COLLECTION,
                BookingStage.PHONE_COLLECTION,
                BookingStage.SERVICE_COLLECTION,
                BookingStage.DATE_COLLECTION,
                BookingStage.TIME_COLLECTION,
                BookingStage.DOCTOR_COLLECTION,
                BookingStage.NOTES_COLLECTION,
            }:
                self._pending_action = PendingAction.ASK_BOOKING_FIELD
            elif stage == BookingStage.CONFIRMATION:
                self._pending_action = PendingAction.WAIT_FOR_CALLER
            log_event(
                logger,
                "booking_stage_changed",
                session_id=self._session_id,
                request_id=request_id,
                previous_stage=previous_stage.value,
                current_stage=stage.value,
                reason=reason,
                pending_fields=list(pending_fields),
            )
            return True

    def set_booking_stage_from_pending(
        self,
        pending_fields: tuple[str, ...],
        *,
        request_id: str | None = None,
        reason: str = "booking_fields_updated",
    ) -> BookingStage:
        stage = booking_stage_from_pending_fields(pending_fields)
        self.set_booking_stage(
            stage,
            reason=reason,
            request_id=request_id,
            pending_fields=pending_fields,
        )
        return stage

    def should_emit_ai_response(
        self,
        text: str,
        *,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> bool:
        return self._repetition_guard.should_emit(
            text,
            request_id=request_id,
            response_id=response_id,
        )

    def should_emit_recovery(
        self,
        text: str,
        *,
        request_id: str | None = None,
    ) -> bool:
        return self._repetition_guard.should_emit(
            text,
            request_id=request_id,
            category="recovery",
        )

    def should_emit_silence_prompt(
        self,
        text: str,
        *,
        request_id: str | None = None,
    ) -> bool:
        return self._repetition_guard.should_emit(
            text,
            request_id=request_id,
            category="silence",
        )

    def guard_state_consistency(
        self,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> bool:
        with self._lock:
            if self._current_state == ConversationState.SPEAKING and not self._ai_speaking:
                previous_state = self._current_state
                self._previous_state = previous_state
                self._current_state = ConversationState.LISTENING
                self._pending_action = PendingAction.WAIT_FOR_CALLER
                self._state_generation += 1
                log_event(
                    logger,
                    "state_guard_triggered",
                    session_id=self._session_id,
                    request_id=request_id,
                    guard_name="stale_speaking_state",
                    previous_state=previous_state.value,
                    current_state=self._current_state.value,
                    reason=reason,
                    state_generation=self._state_generation,
                )
                return True

            if (
                self._current_state == ConversationState.INTERRUPTED
                and not self._caller_speaking
                and not self._ai_speaking
            ):
                previous_state = self._current_state
                self._previous_state = previous_state
                self._current_state = ConversationState.LISTENING
                self._pending_action = PendingAction.WAIT_FOR_CALLER
                self._state_generation += 1
                log_event(
                    logger,
                    "state_guard_triggered",
                    session_id=self._session_id,
                    request_id=request_id,
                    guard_name="stale_interrupted_state",
                    previous_state=previous_state.value,
                    current_state=self._current_state.value,
                    reason=reason,
                    state_generation=self._state_generation,
                )
                return True

            if self._current_state == ConversationState.THINKING and self._caller_speaking:
                log_event(
                    logger,
                    "state_guard_triggered",
                    session_id=self._session_id,
                    request_id=request_id,
                    guard_name="caller_speech_during_thinking",
                    current_state=self._current_state.value,
                    reason=reason,
                )
                return True

        return False

    def mark_recovered(
        self,
        *,
        request_id: str | None,
        recovery_text: str | None,
        reason: str,
    ) -> None:
        log_event(
            logger,
            "conversation_recovered",
            session_id=self._session_id,
            request_id=request_id,
            current_state=self.current_state.value,
            booking_stage=self.booking_stage.value,
            reason=reason,
            recovery_text=recovery_text,
        )


def booking_stage_from_pending_fields(pending_fields: tuple[str, ...]) -> BookingStage:
    if not pending_fields:
        return BookingStage.CONFIRMATION
    pending = set(pending_fields)
    if "customer_name" in pending:
        return BookingStage.CUSTOMER_COLLECTION
    if "phone_number" in pending:
        return BookingStage.PHONE_COLLECTION
    if "service_type" in pending:
        return BookingStage.SERVICE_COLLECTION
    if "appointment_date" in pending:
        return BookingStage.DATE_COLLECTION
    if "appointment_time" in pending:
        return BookingStage.TIME_COLLECTION
    if "doctor_preference" in pending:
        return BookingStage.DOCTOR_COLLECTION
    if "notes" in pending or "optional_notes" in pending:
        return BookingStage.NOTES_COLLECTION
    return BookingStage.CONFIRMATION
