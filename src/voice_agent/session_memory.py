from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from voice_agent.booking.entities import BookingField, BookingFieldValue, BookingStateSnapshot
from voice_agent.conversation.orchestrator import booking_stage_from_pending_fields
from voice_agent.conversation.states import BookingStage
from voice_agent.logging_config import get_logger, log_event
from voice_agent.memory.call_memory_engine import CallMemoryEngine

logger = get_logger(__name__)

BOOKING_REQUIRED_FIELDS = (
    "customer_name",
    "phone_number",
    "service_type",
    "appointment_date",
    "appointment_time",
    "doctor_preference",
)
BOOKING_OPTIONAL_FIELDS = ("notes",)

_ATTR_BY_FIELD = {
    "customer_name": "caller_name",
    "phone_number": "phone_number",
    "service_type": "selected_service",
    "appointment_date": "preferred_date",
    "appointment_time": "preferred_time",
    "doctor_preference": "doctor_preference",
    "notes": "optional_notes",
    "optional_notes": "optional_notes",
    "language": "language",
}


@dataclass
class BookingMemory:
    caller_name: str | None = None
    phone_number: str | None = None
    selected_service: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    doctor_preference: str | None = None
    optional_notes: str | None = None
    language: str | None = None
    awaiting_confirmation: bool = False
    confirmation_completed: bool = False
    last_summary_fingerprint: str | None = None
    notes_requested: bool = False
    field_confidence: dict[str, float] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    active_correction: str | None = None

    @property
    def pending_booking_fields(self) -> tuple[str, ...]:
        pending: list[str] = []
        if self.caller_name is None:
            pending.append("customer_name")
        if self.phone_number is None:
            pending.append("phone_number")
        if self.selected_service is None:
            pending.append("service_type")
        if self.preferred_date is None:
            pending.append("appointment_date")
        if self.preferred_time is None:
            pending.append("appointment_time")
        if self.doctor_preference is None:
            pending.append("doctor_preference")
        if self.optional_notes is None:
            pending.append("notes")
        return tuple(pending)

    @property
    def required_fields_complete(self) -> bool:
        return all(
            getattr(self, attr) is not None
            for attr in (
                "caller_name",
                "phone_number",
                "selected_service",
                "preferred_date",
                "preferred_time",
                "doctor_preference",
            )
        )

    @property
    def is_complete(self) -> bool:
        return self.required_fields_complete and self.optional_notes is not None


@dataclass
class CallSessionMemory:
    session_id: str
    language: str = "english"
    booking: BookingMemory = field(default_factory=BookingMemory)
    booking_stage: BookingStage = BookingStage.IDLE
    escalation_triggered: bool = False
    escalation_reason: str | None = None
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    max_turns: int = 16
    last_faq_retrieval: dict[str, Any] | None = None
    rolling_context_summary: str = ""
    compressed_context_turns: int = 0
    pruned_turn_count: int = 0
    runtime_memory: CallMemoryEngine = field(init=False)

    def __post_init__(self) -> None:
        self.runtime_memory = CallMemoryEngine(self.session_id)
        self.runtime_memory.update_from_session(self)

    @property
    def caller_name(self) -> str | None:
        return self.booking.caller_name

    @property
    def selected_service(self) -> str | None:
        return self.booking.selected_service

    @property
    def phone_number(self) -> str | None:
        return self.booking.phone_number

    @property
    def preferred_date(self) -> str | None:
        return self.booking.preferred_date

    @property
    def preferred_time(self) -> str | None:
        return self.booking.preferred_time

    @property
    def doctor_preference(self) -> str | None:
        return self.booking.doctor_preference

    @property
    def optional_notes(self) -> str | None:
        return self.booking.optional_notes

    @property
    def notes(self) -> str | None:
        return self.booking.optional_notes

    @property
    def pending_booking_fields(self) -> tuple[str, ...]:
        return self.booking.pending_booking_fields

    def update_language(self, language: str, *, request_id: str | None = None) -> None:
        if language == self.language:
            return
        previous_language = self.language
        self.language = language
        self.runtime_memory.update_language(language, request_id=request_id)
        self._log_update(
            request_id=request_id,
            updated_fields=("language",),
            previous_language=previous_language,
        )

    def mark_escalation(
        self,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> None:
        cleaned_reason = " ".join(reason.strip().split()) or "unspecified"
        if self.escalation_triggered and self.escalation_reason == cleaned_reason:
            return
        self.escalation_triggered = True
        self.escalation_reason = cleaned_reason
        self.runtime_memory.note_escalation(
            reason=cleaned_reason,
            request_id=request_id,
        )
        self._log_update(
            request_id=request_id,
            updated_fields=("escalation",),
            escalation_reason=cleaned_reason,
        )

    def capture_booking_fields(
        self,
        *,
        request_id: str | None = None,
        **fields: str | None,
    ) -> tuple[str, ...]:
        captured: list[str] = []
        for field_name, value in fields.items():
            attr = _ATTR_BY_FIELD.get(field_name)
            if attr is None or value is None:
                continue
            text = " ".join(str(value).strip().split())
            if not text and field_name not in {"optional_notes", "notes"}:
                continue
            current = getattr(self.booking, attr)
            if current is not None:
                continue
            setattr(self.booking, attr, text)
            canonical_field = "notes" if field_name == "optional_notes" else field_name
            captured.append(canonical_field)
            log_event(
                logger,
                "booking_field_captured",
                request_id=request_id,
                session_id=self.session_id,
                field_name=canonical_field,
                pending_booking_fields=list(self.pending_booking_fields),
            )

        if captured:
            self.update_booking_stage(
                request_id=request_id,
                reason="booking_field_captured",
            )
            self.runtime_memory.update_from_session(self, request_id=request_id)
            self._log_update(
                request_id=request_id,
                updated_fields=tuple(captured),
            )
        return tuple(captured)

    def apply_booking_values(
        self,
        values: tuple[BookingFieldValue, ...],
        *,
        request_id: str | None = None,
        correction_fields: tuple[BookingField, ...] = (),
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        captured: list[str] = []
        corrected: list[str] = []
        for value in values:
            field_name = value.field.value
            attr = _ATTR_BY_FIELD.get(field_name)
            if attr is None:
                continue
            text = " ".join(value.value.strip().split())
            if not text and value.field != BookingField.NOTES:
                continue

            current = getattr(self.booking, attr)
            if current == text:
                self.booking.field_confidence[field_name] = value.confidence
                self.booking.field_sources[field_name] = value.source_text[:160]
                continue
            if current is not None and value.field not in correction_fields:
                continue

            setattr(self.booking, attr, text)
            self.booking.field_confidence[field_name] = value.confidence
            self.booking.field_sources[field_name] = value.source_text[:160]
            if current is None:
                captured.append(field_name)
            else:
                corrected.append(field_name)

        if captured or corrected:
            self.booking.active_correction = ",".join(corrected) if corrected else None
            self.booking.confirmation_completed = False
            self.booking.awaiting_confirmation = False
            self.runtime_memory.note_corrections(
                tuple(corrected),
                request_id=request_id,
            )
            self.update_booking_stage(
                request_id=request_id,
                reason="booking_values_applied",
            )
            self.runtime_memory.update_from_session(self, request_id=request_id)
            self._log_update(
                request_id=request_id,
                updated_fields=tuple(captured + corrected),
                corrected_fields=tuple(corrected),
            )
        return tuple(captured), tuple(corrected)

    def booking_values(self) -> dict[BookingField, str]:
        values: dict[BookingField, str] = {}
        if self.booking.caller_name is not None:
            values[BookingField.CUSTOMER_NAME] = self.booking.caller_name
        if self.booking.phone_number is not None:
            values[BookingField.PHONE_NUMBER] = self.booking.phone_number
        if self.booking.selected_service is not None:
            values[BookingField.SERVICE_TYPE] = self.booking.selected_service
        if self.booking.preferred_date is not None:
            values[BookingField.APPOINTMENT_DATE] = self.booking.preferred_date
        if self.booking.preferred_time is not None:
            values[BookingField.APPOINTMENT_TIME] = self.booking.preferred_time
        if self.booking.doctor_preference is not None:
            values[BookingField.DOCTOR_PREFERENCE] = self.booking.doctor_preference
        if self.booking.optional_notes is not None:
            values[BookingField.NOTES] = self.booking.optional_notes
        if self.booking.language is not None:
            values[BookingField.LANGUAGE] = self.booking.language
        return values

    def booking_state_snapshot(self) -> BookingStateSnapshot:
        values = self.booking_values()
        pending_required = tuple(
            BookingField(field)
            for field in BOOKING_REQUIRED_FIELDS
            if BookingField(field) not in values
        )
        pending_optional = (
            (BookingField.NOTES,) if self.booking.optional_notes is None else ()
        )
        return BookingStateSnapshot(
            values=values,
            pending_required_fields=pending_required,
            pending_optional_fields=pending_optional,
            awaiting_confirmation=self.booking.awaiting_confirmation,
            confirmation_completed=self.booking.confirmation_completed,
            stage=self.booking_stage.value,
            language=self.booking.language or self.language,
        )

    def mark_notes_requested(self) -> None:
        self.booking.notes_requested = True
        self.runtime_memory.mark_unresolved_question(field_name="notes")

    def mark_awaiting_confirmation(self, fingerprint: str) -> None:
        self.booking.awaiting_confirmation = True
        self.booking.last_summary_fingerprint = fingerprint
        self.runtime_memory.mark_unresolved_question(field_name="confirmation")
        self.runtime_memory.update_from_session(self)

    def mark_confirmation_completed(self) -> None:
        self.booking.awaiting_confirmation = False
        self.booking.confirmation_completed = True
        self.booking.active_correction = None
        self.runtime_memory.resolve_question(field_name="confirmation")
        self.runtime_memory.update_from_session(self)

    def clear_confirmation(self) -> None:
        self.booking.awaiting_confirmation = False
        self.booking.confirmation_completed = False
        self.runtime_memory.resolve_question(field_name="confirmation")
        self.runtime_memory.update_from_session(self)

    def update_booking_stage(
        self,
        *,
        request_id: str | None = None,
        reason: str = "booking_memory_updated",
    ) -> BookingStage:
        next_stage = booking_stage_from_pending_fields(self.pending_booking_fields)
        if next_stage == self.booking_stage:
            return self.booking_stage

        previous_stage = self.booking_stage
        self.booking_stage = next_stage
        log_event(
            logger,
            "booking_stage_changed",
            request_id=request_id,
            session_id=self.session_id,
            previous_stage=previous_stage.value,
            current_stage=next_stage.value,
            reason=reason,
            pending_booking_fields=list(self.pending_booking_fields),
        )
        return self.booking_stage

    def record_turn(
        self,
        *,
        role: str,
        text: str,
        request_id: str | None = None,
    ) -> None:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            return
        self.recent_turns.append({"role": role, "text": cleaned[:240]})
        del self.recent_turns[:-self.max_turns]
        self.runtime_memory.record_turn(
            role=role,
            text=cleaned,
            request_id=request_id,
        )
        self._log_update(
            request_id=request_id,
            updated_fields=("recent_turns",),
            recent_turn_count=len(self.recent_turns),
        )

    def record_faq_retrieval(
        self,
        *,
        matched_count: int,
        confidence: float,
        injected_chars: int,
        latency_ms: float,
        source: str,
        selected_questions: tuple[str, ...],
        request_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self.last_faq_retrieval = {
            "matched_count": matched_count,
            "confidence": confidence,
            "injected_chars": injected_chars,
            "latency_ms": latency_ms,
            "source": source,
            "selected_questions": selected_questions,
            "failure_reason": failure_reason,
        }
        self._log_update(
            request_id=request_id,
            updated_fields=("faq_retrieval",),
            faq_retrieval_matched_count=matched_count,
            faq_retrieval_confidence=confidence,
            faq_retrieval_injected_chars=injected_chars,
            faq_retrieval_latency_ms=latency_ms,
            faq_retrieval_source=source,
            faq_retrieval_failure_reason=failure_reason,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "language": self.language,
            "caller_name": self.booking.caller_name,
            "selected_service": self.booking.selected_service,
            "phone_number": self.booking.phone_number,
            "preferred_date": self.booking.preferred_date,
            "preferred_time": self.booking.preferred_time,
            "doctor_preference": self.booking.doctor_preference,
            "optional_notes": self.booking.optional_notes,
            "language": self.booking.language,
            "awaiting_confirmation": self.booking.awaiting_confirmation,
            "confirmation_completed": self.booking.confirmation_completed,
            "booking_stage": self.booking_stage.value,
            "pending_booking_fields": self.pending_booking_fields,
            "escalation_triggered": self.escalation_triggered,
            "escalation_reason": self.escalation_reason,
            "runtime_memory": {
                "turn_index": self.runtime_memory.turn_index,
                "compressed_turns": self.runtime_memory.snapshot().compressed_turns,
                "unresolved_questions": [
                    {
                        "field_name": question.field_name,
                        "attempts": question.attempts,
                    }
                    for question in self.runtime_memory.unresolved_questions()
                ],
            },
            "optimization": {
                "rolling_context_summary": self.rolling_context_summary,
                "compressed_context_turns": self.compressed_context_turns,
                "pruned_turn_count": self.pruned_turn_count,
                "recent_turn_count": len(self.recent_turns),
            },
            "last_faq_retrieval": self.last_faq_retrieval,
        }

    def _log_update(
        self,
        *,
        request_id: str | None,
        updated_fields: tuple[str, ...],
        **extra: Any,
    ) -> None:
        log_event(
            logger,
            "session_memory_updated",
            request_id=request_id,
            session_id=self.session_id,
            language=self.language,
            updated_fields=list(updated_fields),
            caller_name_known=self.booking.caller_name is not None,
            phone_number_known=self.booking.phone_number is not None,
            selected_service_known=self.booking.selected_service is not None,
            preferred_date_known=self.booking.preferred_date is not None,
            preferred_time_known=self.booking.preferred_time is not None,
            doctor_preference_known=self.booking.doctor_preference is not None,
            optional_notes_known=self.booking.optional_notes is not None,
            booking_stage=self.booking_stage.value,
            pending_booking_fields=list(self.pending_booking_fields),
            escalation_triggered=self.escalation_triggered,
            memory_growth=self.runtime_memory.turn_index,
            runtime_memory_turns=self.runtime_memory.turn_index,
            compressed_turns=self.runtime_memory.snapshot().compressed_turns,
            **extra,
        )
