from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Callable, Protocol

from voice_agent.booking.legacy import (
    BOOKING_FAILURE_RESPONSE,
    BookingCalendar,
    BookingConfirmationEvent,
)
from voice_agent.booking.utils import (
    _extract_date,
    _extract_time_preference,
    _find_matching_slot,
    _format_slot,
    _timezone_for,
)
from voice_agent.config import BusinessConfig, CalComConfig
from voice_agent.fulfillment import NotificationFulfillmentState
from voice_agent.logging_config import get_logger, log_event
from voice_agent.providers.calcom import CalComAPIError, CalComBookingRequest, CalComClient
from voice_agent.session_memory import CallSessionMemory
from voice_agent.validation import RuntimeIntegrityValidator

logger = get_logger(__name__)

_CONFIRMATION_STATUSES = {"accepted", "confirmed", "confirmed_pending_payment"}


class BookingNotificationSink(Protocol):
    async def enqueue_booking_confirmation(
        self,
        event: BookingConfirmationEvent,
        request_id: str | None,
    ) -> object:
        ...


class BookingRuntimePersistence(Protocol):
    async def find_booking_by_fingerprint(self, fingerprint: str) -> object | None:
        ...

    async def reserve_booking(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        request_id: str | None = None,
    ) -> bool:
        ...

    async def persist_booking_confirmed(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        calcom_uid: str,
        external_status: str | None,
        booking_time: datetime,
        validation_state: str,
        request_id: str | None = None,
    ) -> bool:
        ...


@dataclass(frozen=True)
class BookingRuntimeResult:
    booking_success: bool
    calcom_booking_uid: str | None
    external_status: str | None
    booking_time: datetime | None
    validation_status: str
    duplicate_detected: bool
    retry_state: str
    persistence_status: str
    booking_fingerprint: str | None = None
    response_text: str | None = None
    failure_reason: str | None = None
    notification_state: NotificationFulfillmentState | None = None
    fulfillment_status: str = "not_required"


class DuplicateBookingGuard:
    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds
        self._in_progress: dict[str, float] = {}
        self._completed: dict[str, tuple[BookingRuntimeResult, float]] = {}

    async def start(self, fingerprint: str) -> BookingRuntimeResult | None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            self._cleanup_locked(now)
            completed_entry = self._completed.get(fingerprint)
            if completed_entry is not None:
                completed = completed_entry[0]
                return BookingRuntimeResult(
                    booking_success=completed.booking_success,
                    calcom_booking_uid=completed.calcom_booking_uid,
                    external_status=completed.external_status,
                    booking_time=completed.booking_time,
                    validation_status=completed.validation_status,
                    duplicate_detected=True,
                    retry_state="duplicate_returned_existing",
                    persistence_status=completed.persistence_status,
                    booking_fingerprint=completed.booking_fingerprint,
                    response_text=completed.response_text,
                    failure_reason=completed.failure_reason,
                    notification_state=completed.notification_state,
                    fulfillment_status=completed.fulfillment_status,
                )
            if fingerprint in self._in_progress:
                return BookingRuntimeResult(
                    booking_success=False,
                    calcom_booking_uid=None,
                    external_status=None,
                    booking_time=None,
                    validation_status="duplicate_in_progress",
                    duplicate_detected=True,
                    retry_state="duplicate_in_progress",
                    persistence_status="not_attempted",
                    booking_fingerprint=fingerprint,
                    response_text=BOOKING_FAILURE_RESPONSE,
                    failure_reason="duplicate_in_progress",
                )
            self._in_progress[fingerprint] = now
            return None

    async def finish(self, fingerprint: str, result: BookingRuntimeResult) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            self._cleanup_locked(now)
            self._in_progress.pop(fingerprint, None)
            if result.calcom_booking_uid is not None:
                self._completed[fingerprint] = (result, now)

    async def fail(self, fingerprint: str, result: BookingRuntimeResult) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            self._cleanup_locked(now)
            self._in_progress.pop(fingerprint, None)
            if result.calcom_booking_uid is not None:
                self._completed[fingerprint] = (result, now)

    async def cleanup(self) -> None:
        async with self._lock:
            self._cleanup_locked(asyncio.get_running_loop().time())

    def _cleanup_locked(self, now: float) -> None:
        expires_before = now - self._ttl_seconds
        self._in_progress = {
            fingerprint: created_at
            for fingerprint, created_at in self._in_progress.items()
            if created_at >= expires_before
        }
        self._completed = {
            fingerprint: entry
            for fingerprint, entry in self._completed.items()
            if entry[1] >= expires_before
        }


GLOBAL_DUPLICATE_GUARD = DuplicateBookingGuard()


class BookingRuntimeExecutor:
    def __init__(
        self,
        *,
        business_config: BusinessConfig,
        calcom_config: CalComConfig | None,
        calendar: BookingCalendar | None = None,
        persistence: BookingRuntimePersistence | None = None,
        notification_sink: BookingNotificationSink | None = None,
        now_provider: Callable[[], datetime] | None = None,
        duplicate_guard: DuplicateBookingGuard | None = None,
    ) -> None:
        self._business_config = business_config
        self._calcom_config = calcom_config
        self._calendar = calendar or (CalComClient(calcom_config) if calcom_config else None)
        self._owns_calendar = calendar is None and self._calendar is not None
        self._persistence = persistence
        self._notification_sink = notification_sink
        self._now_provider = now_provider
        self._duplicate_guard = duplicate_guard or GLOBAL_DUPLICATE_GUARD
        self._validator = RuntimeIntegrityValidator()

    async def aclose(self) -> None:
        if self._owns_calendar and self._calendar is not None:
            await self._calendar.aclose()

    async def confirm(
        self,
        *,
        memory: CallSessionMemory,
        language: str,
        request_id: str | None,
    ) -> BookingRuntimeResult:
        log_event(
            logger,
            "booking_started",
            request_id=request_id,
            call_id=memory.session_id,
            session_id=memory.session_id,
            booking_stage=memory.booking_stage.value,
        )
        prepared = self._prepare(memory)
        if isinstance(prepared, BookingRuntimeResult):
            return prepared
        fingerprint, booking_date, preference = prepared

        duplicate = await self._duplicate_guard.start(fingerprint)
        if duplicate is not None:
            log_event(
                logger,
                "booking_duplicate_detected",
                request_id=request_id,
                call_id=memory.session_id,
                session_id=memory.session_id,
                booking_fingerprint=fingerprint,
                retry_state=duplicate.retry_state,
            )
            return self._with_safe_response(duplicate, language=language)

        try:
            result = await self._confirm_reserved(
                memory=memory,
                booking_fingerprint=fingerprint,
                booking_date=booking_date,
                preference=preference,
                language=language,
                request_id=request_id,
            )
        except Exception as exc:
            log_event(
                logger,
                "booking_failed",
                request_id=request_id,
                call_id=memory.session_id,
                session_id=memory.session_id,
                booking_fingerprint=fingerprint,
                reason="runtime_exception",
                error_type=type(exc).__name__,
            )
            result = BookingRuntimeResult(
                booking_success=False,
                calcom_booking_uid=None,
                external_status=None,
                booking_time=None,
                validation_status="runtime_exception",
                duplicate_detected=False,
                retry_state="failed",
                persistence_status="not_attempted",
                booking_fingerprint=fingerprint,
                response_text=_localized_failure(language),
                failure_reason="runtime_exception",
            )

        if result.booking_success:
            await self._duplicate_guard.finish(fingerprint, result)
        else:
            await self._duplicate_guard.fail(fingerprint, result)
        return self._with_safe_response(result, language=language)

    async def _confirm_reserved(
        self,
        *,
        memory: CallSessionMemory,
        booking_fingerprint: str,
        booking_date: date,
        preference: object,
        language: str,
        request_id: str | None,
    ) -> BookingRuntimeResult:
        if self._calcom_config is None or self._calendar is None or not self._calcom_config.is_configured:
            return self._failure(
                "calcom_not_configured",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
            )
        if not self._calcom_config.default_attendee_email:
            return self._failure(
                "default_attendee_email_missing",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
            )
        if self._persistence is None:
            return self._failure(
                "persistence_not_configured",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
                persistence_status="missing",
            )

        existing = await self._persistence.find_booking_by_fingerprint(booking_fingerprint)
        if existing is not None and getattr(existing, "calcom_uid", None):
            result = self._result_from_existing(
                existing,
                booking_fingerprint=booking_fingerprint,
                language=language,
            )
            log_event(
                logger,
                "booking_duplicate_detected",
                request_id=request_id,
                session_id=memory.session_id,
                booking_fingerprint=booking_fingerprint,
                calcom_booking_uid=result.calcom_booking_uid,
            )
            return result

        reserved = await self._persistence.reserve_booking(
            fingerprint=booking_fingerprint,
            memory=memory,
            request_id=request_id,
        )
        if not reserved:
            log_event(
                logger,
                "booking_duplicate_detected",
                request_id=request_id,
                session_id=memory.session_id,
                booking_fingerprint=booking_fingerprint,
                reason="reservation_conflict",
            )
            existing = await self._persistence.find_booking_by_fingerprint(booking_fingerprint)
            if existing is not None and getattr(existing, "calcom_uid", None):
                return self._result_from_existing(
                    existing,
                    booking_fingerprint=booking_fingerprint,
                    language=language,
                )
            return self._failure(
                "duplicate_booking_in_progress",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
                duplicate_detected=True,
            )

        timezone_name = self._calcom_config.time_zone
        tz = _timezone_for(timezone_name)
        search_end = booking_date + timedelta(days=1)
        try:
            slots = await self._calendar.get_available_slots(
                start=booking_date,
                end=search_end,
                request_id=request_id,
            )
        except Exception as exc:
            return self._failure(
                "availability_check_failed",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
                error=exc,
            )
        slot = _find_matching_slot(
            slots,
            preferred_date=booking_date,
            preference=preference,
            tz=tz,
        )
        if slot is None:
            return self._failure(
                "invalid_slot",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
                response_text=_localized_invalid_slot(language),
            )

        booking = memory.booking
        try:
            confirmation = await self._calendar.create_booking(
                CalComBookingRequest(
                    start=slot.start,
                    attendee_name=booking.caller_name or "",
                    attendee_phone=booking.phone_number or "",
                    attendee_email=self._calcom_config.default_attendee_email,
                ),
                request_id=request_id,
            )
        except Exception as exc:
            return self._failure(
                "booking_create_failed",
                booking_fingerprint=booking_fingerprint,
                language=language,
                request_id=request_id,
                error=exc,
            )

        validation_state = _validation_state(confirmation.uid, confirmation.status)
        if validation_state != "valid":
            return BookingRuntimeResult(
                booking_success=False,
                calcom_booking_uid=confirmation.uid,
                external_status=confirmation.status,
                booking_time=confirmation.start,
                validation_status=validation_state,
                duplicate_detected=False,
                retry_state="failed",
                persistence_status="reserved",
                booking_fingerprint=booking_fingerprint,
                response_text=_localized_failure(language),
                failure_reason=validation_state,
            )

        persisted = await self._persistence.persist_booking_confirmed(
            fingerprint=booking_fingerprint,
            memory=memory,
            calcom_uid=confirmation.uid or "",
            external_status=confirmation.status,
            booking_time=confirmation.start,
            validation_state=validation_state,
            request_id=request_id,
        )
        persistence_status = "persisted" if persisted else "failed"
        if persisted:
            log_event(
                logger,
                "booking_persisted",
                request_id=request_id,
                call_id=memory.session_id,
                session_id=memory.session_id,
                booking_fingerprint=booking_fingerprint,
                calcom_booking_uid=confirmation.uid,
                external_status=confirmation.status,
                booking_validation_state=validation_state,
            )
        result = BookingRuntimeResult(
            booking_success=persisted,
            calcom_booking_uid=confirmation.uid,
            external_status=confirmation.status,
            booking_time=confirmation.start,
            validation_status=validation_state,
            duplicate_detected=False,
            retry_state="complete" if persisted else "external_created_persistence_failed",
            persistence_status=persistence_status,
            booking_fingerprint=booking_fingerprint,
            response_text=(
                _localized_success(
                    language,
                    slot_text=_format_slot(slot.start, self._now(), tz),
                )
                if persisted
                else _localized_failure(language)
            ),
            failure_reason=None if persisted else "persistence_failed",
        )
        if not self._validator.validate_booking(result):
            log_event(
                logger,
                "booking_failed",
                request_id=request_id,
                session_id=memory.session_id,
                booking_fingerprint=booking_fingerprint,
                reason=result.failure_reason or "output_validation_failed",
                calcom_booking_uid=result.calcom_booking_uid,
                persistence_status=result.persistence_status,
            )
            return result

        log_event(
            logger,
            "booking_validated",
            request_id=request_id,
            call_id=memory.session_id,
            session_id=memory.session_id,
            booking_fingerprint=booking_fingerprint,
            calcom_booking_uid=result.calcom_booking_uid,
            external_status=result.external_status,
            validation_status=result.validation_status,
        )
        log_event(
            logger,
            "booking_confirmed",
            request_id=request_id,
            call_id=memory.session_id,
            session_id=memory.session_id,
            booking_fingerprint=booking_fingerprint,
            calcom_booking_uid=result.calcom_booking_uid,
            external_status=result.external_status,
        )
        result = await self._send_sms_if_configured(
            result,
            memory=memory,
            language=language,
            request_id=request_id,
        )
        if not self._validator.validate_fulfillment(
            booking_result=result,
            notification_state=result.notification_state,
            notification_required=self._notification_sink is not None,
        ):
            log_event(
                logger,
                "fulfillment_partial",
                request_id=request_id,
                call_id=memory.session_id,
                session_id=memory.session_id,
                booking_fingerprint=result.booking_fingerprint,
                calcom_booking_uid=result.calcom_booking_uid,
                notification_status=(
                    result.notification_state.status if result.notification_state else None
                ),
                fulfillment_status=result.fulfillment_status,
            )
        else:
            log_event(
                logger,
                "fulfillment_validated",
                request_id=request_id,
                call_id=memory.session_id,
                session_id=memory.session_id,
                booking_fingerprint=result.booking_fingerprint,
                calcom_booking_uid=result.calcom_booking_uid,
                notification_status=(
                    result.notification_state.status if result.notification_state else None
                ),
                fulfillment_status=result.fulfillment_status,
            )
        return result

    async def _send_sms_if_configured(
        self,
        result: BookingRuntimeResult,
        *,
        memory: CallSessionMemory,
        language: str,
        request_id: str | None,
    ) -> BookingRuntimeResult:
        if self._notification_sink is None or result.booking_time is None:
            return result
        event = BookingConfirmationEvent(
            booking_uid=result.calcom_booking_uid,
            booking_status=result.external_status,
            customer_name=memory.booking.caller_name or "",
            phone_number=memory.booking.phone_number or "",
            business_name=self._business_config.name,
            appointment_start=result.booking_time,
            time_zone=self._calcom_config.time_zone if self._calcom_config else "UTC",
            booking_fingerprint=result.booking_fingerprint,
            fulfillment_language=language,
        )
        delivery = await self._notification_sink.enqueue_booking_confirmation(
            event,
            request_id,
        )
        notification_state = (
            delivery if isinstance(delivery, NotificationFulfillmentState) else None
        )
        queued = (
            notification_state.persisted
            and notification_state.status in {"queued", "sending", "retrying", "sent", "delivered"}
            if notification_state is not None
            else bool(delivery)
        )
        fulfillment_status = (
            notification_state.status
            if notification_state is not None
            else ("queued" if queued else "failed")
        )
        memory.note_notification_state(
            notification_state=notification_state,
            fulfillment_status=fulfillment_status,
            request_id=request_id,
        )
        log_event(
            logger,
            "booking_sms_sent",
            request_id=request_id,
            call_id=memory.session_id,
            session_id=memory.session_id,
            booking_fingerprint=result.booking_fingerprint,
            calcom_booking_uid=result.calcom_booking_uid,
            queued=queued,
            notification_status=fulfillment_status,
            notification_id=(
                notification_state.notification_id if notification_state is not None else None
            ),
            notification_attempts=(
                notification_state.attempts if notification_state is not None else None
            ),
        )
        return BookingRuntimeResult(
            booking_success=result.booking_success,
            calcom_booking_uid=result.calcom_booking_uid,
            external_status=result.external_status,
            booking_time=result.booking_time,
            validation_status=result.validation_status,
            duplicate_detected=result.duplicate_detected,
            retry_state=result.retry_state,
            persistence_status=result.persistence_status,
            booking_fingerprint=result.booking_fingerprint,
            response_text=result.response_text,
            failure_reason=result.failure_reason,
            notification_state=notification_state,
            fulfillment_status=fulfillment_status,
        )

    def _prepare(
        self,
        memory: CallSessionMemory,
    ) -> tuple[str, date, object] | BookingRuntimeResult:
        booking = memory.booking
        missing = [
            name
            for name, value in (
                ("customer_name", booking.caller_name),
                ("phone_number", booking.phone_number),
                ("appointment_date", booking.preferred_date),
                ("appointment_time", booking.preferred_time),
            )
            if value is None
        ]
        if missing:
            return BookingRuntimeResult(
                booking_success=False,
                calcom_booking_uid=None,
                external_status=None,
                booking_time=None,
                validation_status="missing_fields",
                duplicate_detected=False,
                retry_state="collecting",
                persistence_status="not_attempted",
                response_text=BOOKING_FAILURE_RESPONSE,
                failure_reason="missing_fields:" + ",".join(missing),
            )
        now = self._now()
        booking_date = _extract_date(str(booking.preferred_date), now)
        preference = _extract_time_preference(str(booking.preferred_time))
        if booking_date is None or preference is None:
            return BookingRuntimeResult(
                booking_success=False,
                calcom_booking_uid=None,
                external_status=None,
                booking_time=None,
                validation_status="invalid_booking_time",
                duplicate_detected=False,
                retry_state="collecting",
                persistence_status="not_attempted",
                response_text=_localized_invalid_slot(booking.language or "english"),
                failure_reason="invalid_booking_time",
            )
        return (
            build_booking_fingerprint(
                call_id=memory.session_id,
                appointment_date=booking_date.isoformat(),
                appointment_time=_time_key(preference),
                caller_phone=booking.phone_number or "",
            ),
            booking_date,
            preference,
        )

    def _failure(
        self,
        reason: str,
        *,
        booking_fingerprint: str,
        language: str,
        request_id: str | None,
        persistence_status: str = "not_attempted",
        duplicate_detected: bool = False,
        response_text: str | None = None,
        error: Exception | None = None,
    ) -> BookingRuntimeResult:
        log_event(
            logger,
            "booking_failed",
            request_id=request_id,
            booking_fingerprint=booking_fingerprint,
            reason=reason,
            duplicate_detected=duplicate_detected,
            persistence_status=persistence_status,
            error_type=type(error).__name__ if error is not None else None,
            calcom_status_code=(
                error.status_code if isinstance(error, CalComAPIError) else None
            ),
        )
        return BookingRuntimeResult(
            booking_success=False,
            calcom_booking_uid=None,
            external_status=None,
            booking_time=None,
            validation_status=reason,
            duplicate_detected=duplicate_detected,
            retry_state="failed",
            persistence_status=persistence_status,
            booking_fingerprint=booking_fingerprint,
            response_text=response_text or _localized_failure(language),
            failure_reason=reason,
        )

    def _result_from_existing(
        self,
        existing: object,
        *,
        booking_fingerprint: str,
        language: str,
    ) -> BookingRuntimeResult:
        booking_time = _datetime_or_none(getattr(existing, "booking_time", None))
        result = BookingRuntimeResult(
            booking_success=True,
            calcom_booking_uid=getattr(existing, "calcom_uid", None),
            external_status=getattr(existing, "external_status", None),
            booking_time=booking_time,
            validation_status=getattr(existing, "booking_validation_state", None) or "valid",
            duplicate_detected=True,
            retry_state="duplicate_returned_existing",
            persistence_status="persisted",
            booking_fingerprint=booking_fingerprint,
            response_text=_localized_success(
                language,
                slot_text=(
                    _format_slot(
                        booking_time,
                        self._now(),
                        _timezone_for(self._calcom_config.time_zone if self._calcom_config else "UTC"),
                    )
                    if booking_time is not None
                    else "the selected time"
                ),
            ),
            failure_reason=None,
        )
        return result

    def _with_safe_response(
        self,
        result: BookingRuntimeResult,
        *,
        language: str,
    ) -> BookingRuntimeResult:
        response = result.response_text or _localized_failure(language)
        safe_response = self._validator.safe_response(
            response,
            result=result,
            language=language,
        )
        if safe_response == response:
            return result
        return BookingRuntimeResult(
            booking_success=result.booking_success,
            calcom_booking_uid=result.calcom_booking_uid,
            external_status=result.external_status,
            booking_time=result.booking_time,
            validation_status=result.validation_status,
            duplicate_detected=result.duplicate_detected,
            retry_state=result.retry_state,
            persistence_status=result.persistence_status,
            booking_fingerprint=result.booking_fingerprint,
            response_text=safe_response,
            failure_reason=result.failure_reason or "output_validation_failed",
            notification_state=result.notification_state,
            fulfillment_status=result.fulfillment_status,
        )

    def _now(self) -> datetime:
        if self._now_provider is not None:
            value = self._now_provider()
        else:
            value = datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def build_booking_fingerprint(
    *,
    call_id: str,
    appointment_date: str,
    appointment_time: str,
    caller_phone: str,
) -> str:
    raw = "|".join(
        (
            _clean_key(call_id),
            _clean_key(appointment_date),
            _clean_key(appointment_time),
            re.sub(r"\D", "", caller_phone),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validation_state(uid: str | None, status: str | None) -> str:
    if not uid:
        return "missing_calcom_uid"
    if not _external_status_is_success(status):
        return "external_status_not_confirmed"
    return "valid"


def _external_status_is_success(status: str | None) -> bool:
    if status is None:
        return True
    normalized = status.strip().lower()
    return normalized in _CONFIRMATION_STATUSES


def _contains_confirmation_claim(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:appointment|booking)\b.{0,40}\bconfirmed\b|\bconfirmed\b.{0,40}\b(?:appointment|booking)\b",
            text,
            re.I,
        )
    )


def _localized_success(language: str, *, slot_text: str) -> str:
    if language in {"hindi", "hinglish", "mixed"}:
        return f"Done, aapka appointment {slot_text} ke liye confirmed hai."
    return f"Done, your appointment is confirmed for {slot_text}."


def _localized_failure(language: str) -> str:
    if language in {"hindi", "hinglish", "mixed"}:
        return "Sorry, appointment abhi confirm nahi ho paya. Clinic team slot verify karke follow up karegi."
    return BOOKING_FAILURE_RESPONSE


def _localized_invalid_slot(language: str) -> str:
    if language in {"hindi", "hinglish", "mixed"}:
        return "Sorry, woh slot available nahi dikh raha. Kaunsa dusra date aur time chalega?"
    return "Sorry, I could not confirm that slot. Which other date and time would work?"


def _time_key(preference: object) -> str:
    value = getattr(preference, "start", None)
    if isinstance(value, clock_time):
        return value.strftime("%H:%M")
    return str(getattr(preference, "label", value or "")).strip().lower()


def _clean_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _datetime_or_none(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None
