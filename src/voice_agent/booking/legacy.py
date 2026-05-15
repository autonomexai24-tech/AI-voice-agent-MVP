from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_agent.config import BusinessConfig, CalComConfig
from voice_agent.logging_config import get_logger, log_event
from voice_agent.providers.calcom import (
    AvailableSlot,
    CalComAPIError,
    CalComBookingConfirmation,
    CalComBookingRequest,
    CalComClient,
)

logger = get_logger(__name__)

BOOKING_FAILURE_RESPONSE = (
    "Sorry sir, I'm unable to confirm the appointment right now. "
    "Please try again in a few minutes."
)

_BOOKING_INTENT_RE = re.compile(
    r"\b(?:appointment|book|booking|schedule|visit|consultation|consult|come in)\b",
    re.IGNORECASE,
)
_YES_RE = re.compile(r"\b(?:yes|yeah|yep|sure|okay|ok|fine|works|confirm|that works)\b", re.I)
_NO_RE = re.compile(r"\b(?:no|nope|not|doesn't work|does not work|another|different)\b", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{6,}\d)")
_TIME_12H_RE = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", re.I)
_TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b")
_NAME_PATTERNS = (
    re.compile(r"\b(?:my name is|name is|this is|i am|i'm)\s+([A-Za-z][A-Za-z .'-]{1,60})", re.I),
    re.compile(r"\b(?:for|under)\s+([A-Za-z][A-Za-z .'-]{1,60})\s+(?:sir|madam|please)?$", re.I),
)
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class BookingCalendar(Protocol):
    async def get_available_slots(
        self,
        *,
        start: date,
        end: date,
        request_id: str | None = None,
    ) -> list[AvailableSlot]:
        ...

    async def create_booking(
        self,
        booking: CalComBookingRequest,
        *,
        request_id: str | None = None,
    ) -> CalComBookingConfirmation:
        ...

    async def aclose(self) -> None:
        ...


@dataclass(frozen=True)
class TimePreference:
    label: str
    start: clock_time
    end: clock_time
    exact: bool = False


@dataclass(frozen=True)
class BookingDetails:
    customer_name: str | None = None
    preferred_date: date | None = None
    preferred_time: TimePreference | None = None
    phone_number: str | None = None
    selected_slot: datetime | None = None
    confirmation: CalComBookingConfirmation | None = None

    @property
    def pending_fields(self) -> tuple[str, ...]:
        fields: list[str] = []
        if self.preferred_date is None or self.preferred_time is None:
            fields.append("preferred_date_time")
        if self.customer_name is None:
            fields.append("customer_name")
        if self.phone_number is None:
            fields.append("phone_number")
        return tuple(fields)

    @property
    def is_complete(self) -> bool:
        return not self.pending_fields


@dataclass(frozen=True)
class BookingTurnResult:
    handled: bool
    response_text: str | None
    status: str
    pending_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookingConfirmationEvent:
    booking_uid: str | None
    booking_status: str | None
    customer_name: str
    phone_number: str
    business_name: str
    appointment_start: datetime
    time_zone: str


@dataclass(frozen=True)
class _BookingState:
    status: str = "idle"
    details: BookingDetails = BookingDetails()


@dataclass(frozen=True)
class _ExtractedFields:
    customer_name: str | None = None
    preferred_date: date | None = None
    preferred_time: TimePreference | None = None
    phone_number: str | None = None


class BookingWorkflowOrchestrator:
    def __init__(
        self,
        *,
        config: CalComConfig,
        business_config: BusinessConfig,
        calendar: BookingCalendar | None = None,
        now_provider: Callable[[], datetime] | None = None,
        on_booking_confirmed: Callable[
            [BookingConfirmationEvent, str | None],
            Awaitable[None],
        ]
        | None = None,
    ) -> None:
        self._config = config
        self._business_config = business_config
        self._calendar = calendar or CalComClient(config)
        self._owns_calendar = calendar is None
        self._timezone = _timezone_for(config.time_zone)
        self._now_provider = now_provider or (lambda: datetime.now(self._timezone))
        self._on_booking_confirmed = on_booking_confirmed
        self._state = _BookingState()
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_calendar:
            await self._calendar.aclose()

    async def handle_turn(
        self,
        transcript: str,
        *,
        request_id: str | None = None,
    ) -> BookingTurnResult:
        cleaned = _phone_friendly_text(transcript)
        if not cleaned:
            return BookingTurnResult(False, None, self._state.status)

        async with self._lock:
            if self._state.status in {"confirmed", "failed"} and _has_booking_intent(cleaned):
                self._state = _BookingState()
            elif self._state.status in {"confirmed", "failed"}:
                return BookingTurnResult(False, None, self._state.status)

            if self._state.status == "idle" and not _has_booking_intent(cleaned):
                return BookingTurnResult(False, None, self._state.status)

            if self._state.status == "idle":
                self._state = _BookingState(status="collecting")
                log_event(
                    logger,
                    "booking_workflow_started",
                    request_id=request_id,
                    business_name=self._business_config.name,
                )

            if self._state.status == "awaiting_slot_confirmation":
                return await self._handle_slot_confirmation(cleaned, request_id=request_id)

            extracted = _extract_fields(
                cleaned,
                now=self._now(),
                pending_fields=self._state.details.pending_fields,
            )
            self._apply_extracted_fields(extracted, request_id=request_id)

            if not self._state.details.is_complete:
                response = _next_collection_prompt(self._state.details)
                return BookingTurnResult(
                    True,
                    response,
                    self._state.status,
                    self._state.details.pending_fields,
                )

            return await self._check_and_book(request_id=request_id)

    async def _handle_slot_confirmation(
        self,
        transcript: str,
        *,
        request_id: str | None,
    ) -> BookingTurnResult:
        if _YES_RE.search(transcript):
            if self._state.details.selected_slot is None:
                self._state = replace(self._state, status="collecting")
                return BookingTurnResult(
                    True,
                    "Sorry sir, which date and time would you prefer?",
                    self._state.status,
                    self._state.details.pending_fields,
                )
            return await self._create_booking(request_id=request_id)

        if _NO_RE.search(transcript):
            details = replace(
                self._state.details,
                preferred_time=None,
                selected_slot=None,
            )
            self._state = _BookingState(status="collecting", details=details)
            return BookingTurnResult(
                True,
                "No problem sir. What time would you prefer instead?",
                self._state.status,
                self._state.details.pending_fields,
            )

        return BookingTurnResult(
            True,
            "Would you like me to confirm that slot, sir?",
            self._state.status,
            self._state.details.pending_fields,
        )

    async def _check_and_book(self, *, request_id: str | None) -> BookingTurnResult:
        details = self._state.details
        if details.preferred_date is None or details.preferred_time is None:
            return BookingTurnResult(
                True,
                _next_collection_prompt(details),
                self._state.status,
                details.pending_fields,
            )

        if not self._config.is_configured:
            log_event(
                logger,
                "booking_failure",
                request_id=request_id,
                reason="calcom_not_configured",
            )
            self._state = replace(self._state, status="failed")
            return BookingTurnResult(True, BOOKING_FAILURE_RESPONSE, self._state.status)

        self._state = replace(self._state, status="checking_availability")
        search_end = details.preferred_date + timedelta(days=2)
        try:
            slots = await self._calendar.get_available_slots(
                start=details.preferred_date,
                end=search_end,
                request_id=request_id,
            )
        except Exception as exc:
            log_event(
                logger,
                "booking_failure",
                request_id=request_id,
                reason="availability_check_failed",
                error_type=type(exc).__name__,
            )
            self._state = replace(self._state, status="failed")
            return BookingTurnResult(True, BOOKING_FAILURE_RESPONSE, self._state.status)

        matching_slot = _find_matching_slot(
            slots,
            preferred_date=details.preferred_date,
            preference=details.preferred_time,
            tz=self._timezone,
        )
        if matching_slot is not None and details.preferred_time.exact:
            self._state = replace(
                self._state,
                status="booking",
                details=replace(details, selected_slot=matching_slot.start),
            )
            return await self._create_booking(request_id=request_id)

        if matching_slot is not None:
            self._state = replace(
                self._state,
                status="awaiting_slot_confirmation",
                details=replace(details, selected_slot=matching_slot.start),
            )
            slot_text = _format_slot(matching_slot.start, self._now(), self._timezone)
            return BookingTurnResult(
                True,
                f"We have {slot_text} available, sir. Should I confirm it?",
                self._state.status,
                self._state.details.pending_fields,
            )

        alternatives = _alternative_slots(slots, details.preferred_date, self._timezone)
        if alternatives:
            alternative = alternatives[0]
            self._state = replace(
                self._state,
                status="awaiting_slot_confirmation",
                details=replace(details, selected_slot=alternative.start),
            )
            preferred_text = _format_preference(
                details.preferred_date,
                details.preferred_time,
                self._now().date(),
            )
            alternative_text = _format_slot(alternative.start, self._now(), self._timezone)
            return BookingTurnResult(
                True,
                f"{preferred_text} is full sir. We have {alternative_text} available. Would that work for you?",
                self._state.status,
                self._state.details.pending_fields,
            )

        details = replace(details, preferred_date=None, preferred_time=None)
        self._state = _BookingState(status="collecting", details=details)
        return BookingTurnResult(
            True,
            "Sorry sir, I don't see a slot around that time. Which other date and time would work?",
            self._state.status,
            self._state.details.pending_fields,
        )

    async def _create_booking(self, *, request_id: str | None) -> BookingTurnResult:
        details = self._state.details
        if (
            details.customer_name is None
            or details.phone_number is None
            or details.selected_slot is None
        ):
            self._state = replace(self._state, status="collecting")
            return BookingTurnResult(
                True,
                _next_collection_prompt(details),
                self._state.status,
                details.pending_fields,
            )

        if not self._config.default_attendee_email:
            log_event(
                logger,
                "booking_failure",
                request_id=request_id,
                reason="default_attendee_email_missing",
            )
            self._state = replace(self._state, status="failed")
            return BookingTurnResult(True, BOOKING_FAILURE_RESPONSE, self._state.status)

        try:
            confirmation = await self._calendar.create_booking(
                CalComBookingRequest(
                    start=details.selected_slot,
                    attendee_name=details.customer_name,
                    attendee_phone=details.phone_number,
                    attendee_email=self._config.default_attendee_email,
                ),
                request_id=request_id,
            )
        except Exception as exc:
            log_event(
                logger,
                "booking_failure",
                request_id=request_id,
                reason="booking_create_failed",
                error_type=type(exc).__name__,
                calcom_status_code=(
                    exc.status_code if isinstance(exc, CalComAPIError) else None
                ),
            )
            self._state = replace(self._state, status="failed")
            return BookingTurnResult(True, BOOKING_FAILURE_RESPONSE, self._state.status)

        self._state = _BookingState(
            status="confirmed",
            details=replace(details, confirmation=confirmation),
        )
        slot_text = _format_slot(details.selected_slot, self._now(), self._timezone)
        log_event(
            logger,
            "booking_confirmation",
            request_id=request_id,
            booking_uid=confirmation.uid,
            booking_status=confirmation.status,
        )
        await self._emit_booking_confirmed(
            BookingConfirmationEvent(
                booking_uid=confirmation.uid,
                booking_status=confirmation.status,
                customer_name=details.customer_name,
                phone_number=details.phone_number,
                business_name=self._business_config.name,
                appointment_start=details.selected_slot,
                time_zone=self._config.time_zone,
            ),
            request_id=request_id,
        )
        return BookingTurnResult(
            True,
            f"Done sir, your appointment is confirmed for {slot_text}.",
            self._state.status,
        )

    async def _emit_booking_confirmed(
        self,
        event: BookingConfirmationEvent,
        *,
        request_id: str | None,
    ) -> None:
        if self._on_booking_confirmed is None:
            return
        try:
            await self._on_booking_confirmed(event, request_id)
        except Exception as exc:
            log_event(
                logger,
                "booking_confirmation_notification_failed",
                request_id=request_id,
                booking_uid=event.booking_uid,
                error_type=type(exc).__name__,
            )

    def _apply_extracted_fields(
        self,
        extracted: _ExtractedFields,
        *,
        request_id: str | None,
    ) -> None:
        details = self._state.details
        updates = {}
        collected: list[str] = []
        if extracted.customer_name and details.customer_name is None:
            updates["customer_name"] = extracted.customer_name
            collected.append("customer_name")
        if extracted.preferred_date and details.preferred_date is None:
            updates["preferred_date"] = extracted.preferred_date
            collected.append("preferred_date")
        if extracted.preferred_time and details.preferred_time is None:
            updates["preferred_time"] = extracted.preferred_time
            collected.append("preferred_time")
        if extracted.phone_number and details.phone_number is None:
            updates["phone_number"] = extracted.phone_number
            collected.append("phone_number")

        if not updates:
            return

        details = replace(details, **updates)
        self._state = replace(self._state, details=details)
        for field_name in collected:
            log_event(
                logger,
                "booking_field_collected",
                request_id=request_id,
                field_name=field_name,
                pending_fields=details.pending_fields,
                collected_fields_count=4 - len(details.pending_fields),
            )

    def _now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None:
            return now.replace(tzinfo=self._timezone)
        return now.astimezone(self._timezone)


def _extract_fields(
    transcript: str,
    *,
    now: datetime,
    pending_fields: tuple[str, ...],
) -> _ExtractedFields:
    return _ExtractedFields(
        customer_name=_extract_name(transcript, pending_fields),
        preferred_date=_extract_date(transcript, now),
        preferred_time=_extract_time_preference(transcript),
        phone_number=_extract_phone(transcript),
    )


def _extract_name(transcript: str, pending_fields: tuple[str, ...]) -> str | None:
    without_phone = _PHONE_RE.sub(" ", transcript)
    without_date_time = _TIME_12H_RE.sub(" ", without_phone)
    without_date_time = _TIME_24H_RE.sub(" ", without_date_time)
    for pattern in _NAME_PATTERNS:
        match = pattern.search(without_date_time)
        if match:
            return _clean_name(match.group(1))

    if "customer_name" not in pending_fields:
        return None

    if _has_date_or_time_words(transcript) or _has_booking_intent(transcript):
        return None

    tokens = re.findall(r"[A-Za-z][A-Za-z.'-]*", without_date_time)
    if 1 <= len(tokens) <= 4:
        return _clean_name(" ".join(tokens))
    return None


def _extract_date(transcript: str, now: datetime) -> date | None:
    normalized = transcript.lower()
    today = now.date()
    if "day after tomorrow" in normalized:
        return today + timedelta(days=2)
    if "tomorrow" in normalized:
        return today + timedelta(days=1)
    if "today" in normalized:
        return today

    match = _ISO_DATE_RE.search(normalized)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _SLASH_DATE_RE.search(normalized)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    month_pattern = re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("
        + "|".join(_MONTHS)
        + r")(?:\s+(20\d{2}))?\b",
        re.I,
    )
    match = month_pattern.search(normalized)
    if match:
        day = int(match.group(1))
        month = _MONTHS[match.group(2).lower()]
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    reverse_month_pattern = re.compile(
        r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?\b",
        re.I,
    )
    match = reverse_month_pattern.search(normalized)
    if match:
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    for weekday, index in _WEEKDAYS.items():
        if re.search(rf"\b(?:next\s+)?{weekday}\b", normalized):
            days = (index - today.weekday()) % 7
            if days == 0 or f"next {weekday}" in normalized:
                days += 7
            return today + timedelta(days=days)
    return None


def _extract_time_preference(transcript: str) -> TimePreference | None:
    normalized = transcript.lower()
    match = _TIME_12H_RE.search(normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        meridiem = match.group(3).lower()
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
        return _exact_time(hour, minute)

    match = _TIME_24H_RE.search(normalized)
    if match:
        return _exact_time(int(match.group(1)), int(match.group(2)))

    if "noon" in normalized:
        return _exact_time(12, 0)
    if "morning" in normalized:
        return TimePreference("morning", clock_time(9, 0), clock_time(12, 0))
    if "afternoon" in normalized:
        return TimePreference("afternoon", clock_time(12, 0), clock_time(17, 0))
    if "evening" in normalized:
        return TimePreference("evening", clock_time(17, 0), clock_time(20, 0))
    return None


def _exact_time(hour: int, minute: int) -> TimePreference | None:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    value = clock_time(hour, minute)
    return TimePreference(_format_time(value), value, value, exact=True)


def _extract_phone(transcript: str) -> str | None:
    match = _PHONE_RE.search(transcript)
    if not match:
        return None
    raw = match.group(0)
    has_plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 15:
        return None
    if has_plus:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+91{digits}"
    return digits


def _find_matching_slot(
    slots: list[AvailableSlot],
    *,
    preferred_date: date,
    preference: TimePreference,
    tz: timezone,
) -> AvailableSlot | None:
    for slot in slots:
        local_start = slot.start.astimezone(tz)
        if local_start.date() != preferred_date:
            continue
        start_time = local_start.time().replace(second=0, microsecond=0)
        if preference.exact and start_time == preference.start:
            return slot
        if not preference.exact and preference.start <= start_time < preference.end:
            return slot
    return None


def _alternative_slots(
    slots: list[AvailableSlot],
    preferred_date: date,
    tz: timezone,
) -> list[AvailableSlot]:
    same_day: list[AvailableSlot] = []
    future: list[AvailableSlot] = []
    for slot in slots:
        local_date = slot.start.astimezone(tz).date()
        if local_date == preferred_date:
            same_day.append(slot)
        elif local_date > preferred_date:
            future.append(slot)
    return (same_day or future)[:3]


def _next_collection_prompt(details: BookingDetails) -> str:
    pending = details.pending_fields
    if "preferred_date_time" in pending:
        return "Sure sir, which date and time would you prefer?"
    if "customer_name" in pending:
        return "May I have your name, sir?"
    if "phone_number" in pending:
        return "And your phone number, please?"
    return "One moment sir, I'll check that slot."


def _format_preference(
    preferred_date: date,
    preference: TimePreference,
    today: date,
) -> str:
    date_text = "tomorrow" if preferred_date == today + timedelta(days=1) else "that time"
    if preference.exact:
        return f"{date_text} at {preference.label}"
    return f"{date_text} {preference.label}"


def _format_slot(slot: datetime, now: datetime, tz: timezone) -> str:
    local = slot.astimezone(tz)
    date_text = _format_slot_date(local.date(), now.date())
    time_text = _format_time(local.time())
    if date_text:
        return f"{date_text} at {time_text}"
    return time_text


def _format_slot_date(value: date, today: date) -> str:
    if value == today:
        return "today"
    if value == today + timedelta(days=1):
        return "tomorrow"
    return value.strftime("%A")


def _format_time(value: clock_time) -> str:
    hour = value.hour
    minute = value.minute
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    if minute:
        return f"{display_hour}:{minute:02d} {suffix}"
    return f"{display_hour} {suffix}"


def _clean_name(value: str) -> str | None:
    value = re.split(r"\b(?:for|on|at|tomorrow|today|phone|number)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"[^A-Za-z .'-]", " ", value)
    value = _phone_friendly_text(value)
    value = re.sub(r"\b(?:sir|madam|please|appointment)\b", "", value, flags=re.I)
    value = _phone_friendly_text(value)
    if not value or len(value) < 2:
        return None
    return " ".join(part.capitalize() for part in value.split())


def _has_booking_intent(transcript: str) -> bool:
    return bool(_BOOKING_INTENT_RE.search(transcript))


def _has_date_or_time_words(transcript: str) -> bool:
    normalized = transcript.lower()
    return any(
        word in normalized
        for word in (
            "today",
            "tomorrow",
            "morning",
            "afternoon",
            "evening",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    )


def _phone_friendly_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _timezone_for(name: str) -> timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Kolkata", "Asia/Calcutta"}:
            return timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
        return timezone.utc
