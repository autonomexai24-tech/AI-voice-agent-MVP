from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from voice_agent.booking.confidence import evaluate_booking_confidence
from voice_agent.booking.correction import detect_booking_correction
from voice_agent.booking.entities import (
    BookingField,
    BookingFieldValue,
    BookingIssue,
    REQUIRED_BOOKING_FIELDS,
)
from voice_agent.booking.extraction import (
    ExtractionContext,
    extract_booking_entities,
    has_booking_intent,
    is_confirmation_no,
    is_confirmation_yes,
    looks_like_booking_request,
)
from voice_agent.booking.retry_strategy import (
    prompt_for_confirmation_retry,
    prompt_for_issue,
    prompt_for_missing_field,
    prompt_for_silence,
)
from voice_agent.booking.summaries import build_booking_summary
from voice_agent.booking.validation import validate_booking_extraction
from voice_agent.config import BusinessConfig
from voice_agent.language import SessionLanguageSnapshot, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.runtime_persistence import RuntimePersistenceSink, safe_enqueue
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


@dataclass(frozen=True)
class BookingWorkflowResult:
    handled: bool
    response_text: str | None
    status: str
    captured_fields: tuple[str, ...] = ()
    corrected_fields: tuple[str, ...] = ()
    pending_fields: tuple[str, ...] = ()
    booking_stage: str = "idle"


class BookingIntelligenceWorkflow:
    def __init__(
        self,
        business_config: BusinessConfig,
        *,
        today_provider: Callable[[], date] | None = None,
        persistence_sink: RuntimePersistenceSink | None = None,
    ) -> None:
        self._business_config = business_config
        self._today_provider = today_provider or date.today
        self._persistence_sink = persistence_sink

    async def handle_turn(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
    ) -> BookingWorkflowResult:
        cleaned = _clean_text(transcript)
        language = language or default_language_snapshot()
        active_language = language.active_language
        if memory.booking.language != active_language:
            memory.booking.language = active_language
        memory.runtime_memory.update_language(language, request_id=request_id)
        memory.runtime_memory.update_from_session(
            memory,
            language=language,
            request_id=request_id,
        )

        if not cleaned:
            return self._handle_silence(memory, active_language, request_id=request_id)

        active = _booking_active(memory)
        if memory.booking.confirmation_completed and not _starts_new_or_corrects(
            cleaned,
            self._business_config.services,
        ):
            return BookingWorkflowResult(
                False,
                None,
                "complete",
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )
        if not active and not looks_like_booking_request(
            cleaned,
            self._business_config.services,
        ):
            return BookingWorkflowResult(
                False,
                None,
                "idle",
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )

        memory.update_booking_stage(request_id=request_id, reason="booking_turn_started")
        known_values = memory.booking_values()
        extraction = extract_booking_entities(
            cleaned,
            ExtractionContext(
                pending_fields=memory.pending_booking_fields,
                services=self._business_config.services,
                language=active_language,
                known_values=known_values,
            ),
        )
        _log_extracted_fields(extraction.values, request_id=request_id, memory=memory)
        correction = detect_booking_correction(
            cleaned,
            extraction,
            known_values=known_values,
            awaiting_confirmation=memory.booking.awaiting_confirmation,
        )

        if memory.booking.awaiting_confirmation:
            confirmation_result = self._handle_confirmation_turn(
                cleaned,
                memory=memory,
                extraction_has_booking_values=_has_booking_values(extraction.values),
                correction_fields=correction.fields,
                request_id=request_id,
                language=active_language,
            )
            if confirmation_result is not None:
                return confirmation_result

        validation = validate_booking_extraction(
            extraction,
            known_values=known_values,
            services=self._business_config.services,
            correction_fields=correction.fields,
            current_date=self._today_provider(),
        )
        confidence = evaluate_booking_confidence(
            validation.accepted_values,
            validation.issues,
        )

        blocking_issue = _first_blocking_issue(validation.issues)
        low_confidence_field = confidence.issue.field if confidence.issue else None
        values_to_apply = tuple(
            value
            for value in validation.accepted_values
            if value.field != BookingField.LANGUAGE
            and value.field != low_confidence_field
            and (blocking_issue is None or value.field != blocking_issue.field)
        )
        captured, corrected = memory.apply_booking_values(
            values_to_apply,
            request_id=request_id,
            correction_fields=correction.fields,
        )
        for field_name in corrected:
            log_event(
                logger,
                "booking_field_corrected",
                request_id=request_id,
                session_id=memory.session_id,
                field_name=field_name,
                booking_stage=memory.booking_stage.value,
            )

        if blocking_issue is not None:
            return self._retry_for_issue(
                blocking_issue,
                memory=memory,
                language=active_language,
                request_id=request_id,
                captured=captured,
                corrected=corrected,
            )

        if confidence.is_low_confidence and confidence.issue is not None:
            log_event(
                logger,
                "booking_confidence_low",
                request_id=request_id,
                session_id=memory.session_id,
                field_name=(
                    confidence.issue.field.value if confidence.issue.field is not None else None
                ),
                issue_code=confidence.issue.code,
                value=confidence.issue.value,
                booking_stage=memory.booking_stage.value,
            )
            return self._retry_for_issue(
                confidence.issue,
                memory=memory,
                language=active_language,
                request_id=request_id,
                captured=captured,
                corrected=corrected,
            )

        return self._next_booking_response(
            memory,
            language=active_language,
            request_id=request_id,
            captured=captured,
            corrected=corrected,
        )

    def _handle_confirmation_turn(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        extraction_has_booking_values: bool,
        correction_fields: tuple[BookingField, ...],
        request_id: str | None,
        language: str,
    ) -> BookingWorkflowResult | None:
        if correction_fields or extraction_has_booking_values:
            memory.clear_confirmation()
            return None
        if is_confirmation_yes(transcript):
            memory.mark_confirmation_completed()
            log_event(
                logger,
                "booking_confirmation_completed",
                request_id=request_id,
                session_id=memory.session_id,
                booking_stage=memory.booking_stage.value,
                pending_booking_fields=list(memory.pending_booking_fields),
            )
            safe_enqueue(
                self._persistence_sink,
                "enqueue_booking_confirmed",
                memory=memory,
                confirmation_status="confirmed",
                request_id=request_id,
            )
            self._log_continuity(memory, request_id=request_id)
            return BookingWorkflowResult(
                True,
                _localized(
                    language,
                    "Perfect, I have the appointment details confirmed. The clinic team can confirm the slot shortly.",
                    "Perfect, appointment details confirm ho gaye. Clinic team slot shortly confirm kar degi.",
                ),
                "complete",
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )
        if is_confirmation_no(transcript):
            memory.clear_confirmation()
            prompt = _localized(
                language,
                "No problem. Which detail should I change?",
                "No problem. Kaunsi detail change karni hai?",
            )
            return BookingWorkflowResult(
                True,
                prompt,
                "collecting",
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )

        issue = BookingIssue(
            code="unclear_confirmation",
            field=None,
            message="Confirmation response was unclear.",
            value=transcript,
        )
        return self._retry_for_issue(
            issue,
            memory=memory,
            language=language,
            request_id=request_id,
        )

    def _next_booking_response(
        self,
        memory: CallSessionMemory,
        *,
        language: str,
        request_id: str | None,
        captured: tuple[str, ...] = (),
        corrected: tuple[str, ...] = (),
    ) -> BookingWorkflowResult:
        pending_required = _pending_required_fields(memory)
        if pending_required:
            field = pending_required[0]
            log_event(
                logger,
                "booking_missing_field_detected",
                request_id=request_id,
                session_id=memory.session_id,
                field_name=field.value,
                pending_booking_fields=list(memory.pending_booking_fields),
            )
            memory.runtime_memory.mark_unresolved_question(
                field_name=field.value,
                request_id=request_id,
            )
            self._log_continuity(memory, request_id=request_id)
            return BookingWorkflowResult(
                True,
                prompt_for_missing_field(
                    field,
                    language=language,
                    services=self._business_config.services,
                ),
                "collecting",
                captured_fields=captured,
                corrected_fields=corrected,
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )

        if memory.booking.optional_notes is None:
            if not memory.booking.notes_requested:
                memory.mark_notes_requested()
                log_event(
                    logger,
                    "booking_missing_field_detected",
                    request_id=request_id,
                    session_id=memory.session_id,
                    field_name=BookingField.NOTES.value,
                    optional=True,
                    pending_booking_fields=list(memory.pending_booking_fields),
                )
                memory.runtime_memory.mark_unresolved_question(
                    field_name=BookingField.NOTES.value,
                    request_id=request_id,
                )
                self._log_continuity(memory, request_id=request_id)
                return BookingWorkflowResult(
                    True,
                    prompt_for_missing_field(BookingField.NOTES, language=language),
                    "collecting",
                    captured_fields=captured,
                    corrected_fields=corrected,
                    pending_fields=memory.pending_booking_fields,
                    booking_stage=memory.booking_stage.value,
                )
            memory.apply_booking_values(
                (
                    BookingFieldValue(
                        BookingField.NOTES,
                        "",
                        0.7,
                        "notes skipped",
                        language,
                    ),
                ),
                request_id=request_id,
            )

        return self._request_confirmation(
            memory,
            language=language,
            request_id=request_id,
            captured=captured,
            corrected=corrected,
        )

    def _request_confirmation(
        self,
        memory: CallSessionMemory,
        *,
        language: str,
        request_id: str | None,
        captured: tuple[str, ...],
        corrected: tuple[str, ...],
    ) -> BookingWorkflowResult:
        summary = build_booking_summary(memory.booking_values())
        memory.mark_awaiting_confirmation(summary.fingerprint)
        memory.update_booking_stage(request_id=request_id, reason="booking_summary_ready")
        log_event(
            logger,
            "booking_summary_generated",
            request_id=request_id,
            session_id=memory.session_id,
            summary_fingerprint=summary.fingerprint,
            summary_fields=[field.value for field in memory.booking_values()],
            booking_stage=memory.booking_stage.value,
        )
        log_event(
            logger,
            "booking_confirmation_requested",
            request_id=request_id,
            session_id=memory.session_id,
            summary_fingerprint=summary.fingerprint,
            pending_booking_fields=list(memory.pending_booking_fields),
        )
        self._log_continuity(memory, request_id=request_id)
        return BookingWorkflowResult(
            True,
            summary.text,
            "awaiting_confirmation",
            captured_fields=captured,
            corrected_fields=corrected,
            pending_fields=memory.pending_booking_fields,
            booking_stage=memory.booking_stage.value,
        )

    def _retry_for_issue(
        self,
        issue: BookingIssue,
        *,
        memory: CallSessionMemory,
        language: str,
        request_id: str | None,
        captured: tuple[str, ...] = (),
        corrected: tuple[str, ...] = (),
    ) -> BookingWorkflowResult:
        key = f"{issue.code}:{issue.field.value if issue.field else 'booking'}"
        attempt = memory.booking.retry_counts.get(key, 0) + 1
        memory.booking.retry_counts[key] = attempt
        log_event(
            logger,
            "booking_retry_triggered",
            request_id=request_id,
            session_id=memory.session_id,
            issue_code=issue.code,
            field_name=issue.field.value if issue.field is not None else None,
            attempt=attempt,
            booking_stage=memory.booking_stage.value,
        )
        if issue.field is not None:
            memory.runtime_memory.mark_unresolved_question(
                field_name=issue.field.value,
                request_id=request_id,
            )
        if issue.severity == "error":
            log_event(
                logger,
                "booking_validation_failed",
                request_id=request_id,
                session_id=memory.session_id,
                issue_code=issue.code,
                field_name=issue.field.value if issue.field is not None else None,
                value=issue.value,
                booking_stage=memory.booking_stage.value,
            )
        self._log_continuity(memory, request_id=request_id)
        if issue.code == "unclear_confirmation":
            prompt = prompt_for_confirmation_retry(language=language)
        else:
            prompt = prompt_for_issue(
                issue,
                language=language,
                attempt=attempt,
                services=self._business_config.services,
            )
        return BookingWorkflowResult(
            True,
            prompt,
            "collecting",
            captured_fields=captured,
            corrected_fields=corrected,
            pending_fields=memory.pending_booking_fields,
            booking_stage=memory.booking_stage.value,
        )

    def _handle_silence(
        self,
        memory: CallSessionMemory,
        language: str,
        *,
        request_id: str | None,
    ) -> BookingWorkflowResult:
        if not _booking_active(memory) or memory.booking.confirmation_completed:
            return BookingWorkflowResult(
                False,
                None,
                "idle",
                pending_fields=memory.pending_booking_fields,
                booking_stage=memory.booking_stage.value,
            )
        field = (_pending_required_fields(memory) or (BookingField.NOTES,))[0]
        issue = BookingIssue(
            code="booking_silence",
            field=field,
            message="Silence while collecting booking details.",
        )
        key = f"{issue.code}:{field.value}"
        attempt = memory.booking.retry_counts.get(key, 0) + 1
        memory.booking.retry_counts[key] = attempt
        log_event(
            logger,
            "booking_retry_triggered",
            request_id=request_id,
            session_id=memory.session_id,
            issue_code=issue.code,
            field_name=field.value,
            attempt=attempt,
            booking_stage=memory.booking_stage.value,
        )
        memory.runtime_memory.mark_unresolved_question(
            field_name=field.value,
            request_id=request_id,
        )
        self._log_continuity(memory, request_id=request_id)
        return BookingWorkflowResult(
            True,
            prompt_for_silence(
                field,
                language=language,
                services=self._business_config.services,
            ),
            "collecting",
            pending_fields=memory.pending_booking_fields,
            booking_stage=memory.booking_stage.value,
        )

    def _log_continuity(self, memory: CallSessionMemory, *, request_id: str | None) -> None:
        log_event(
            logger,
            "booking_continuity_preserved",
            request_id=request_id,
            session_id=memory.session_id,
            booking_stage=memory.booking_stage.value,
            awaiting_confirmation=memory.booking.awaiting_confirmation,
            confirmation_completed=memory.booking.confirmation_completed,
            pending_booking_fields=list(memory.pending_booking_fields),
            known_fields=list(memory.booking_values().keys()),
        )


def _log_extracted_fields(
    values: tuple[BookingFieldValue, ...],
    *,
    request_id: str | None,
    memory: CallSessionMemory,
) -> None:
    for value in values:
        if value.field == BookingField.LANGUAGE:
            continue
        log_event(
            logger,
            "booking_field_extracted",
            request_id=request_id,
            session_id=memory.session_id,
            field_name=value.field.value,
            confidence=value.confidence,
            language=value.language,
            booking_stage=memory.booking_stage.value,
        )


def _pending_required_fields(memory: CallSessionMemory) -> tuple[BookingField, ...]:
    values = memory.booking_values()
    return tuple(field for field in REQUIRED_BOOKING_FIELDS if field not in values)


def _has_booking_values(values: tuple[BookingFieldValue, ...]) -> bool:
    return any(value.field != BookingField.LANGUAGE for value in values)


def _first_blocking_issue(issues: tuple[BookingIssue, ...]) -> BookingIssue | None:
    for issue in issues:
        if issue.severity == "error":
            return issue
    return None


def _booking_active(memory: CallSessionMemory) -> bool:
    values = {
        field: value
        for field, value in memory.booking_values().items()
        if field != BookingField.LANGUAGE
    }
    return bool(
        values
        or memory.booking.awaiting_confirmation
        or memory.booking.confirmation_completed
        or memory.booking_stage.value != "idle"
    )


def _starts_new_or_corrects(transcript: str, services: tuple[str, ...]) -> bool:
    return looks_like_booking_request(transcript, services) or bool(
        re.search(r"\b(?:actually|instead|change|make it|rather)\b", transcript, re.I)
    )


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _localized(language: str, english: str, hinglish: str) -> str:
    if language in {"hindi", "hinglish", "mixed"}:
        return hinglish
    return english
