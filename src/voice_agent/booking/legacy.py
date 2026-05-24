from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from voice_agent.booking.utils import TimePreference
from voice_agent.providers.calcom import (
    CalComBookingConfirmation,
    CalComBookingRequest,
)

BOOKING_FAILURE_RESPONSE = (
    "Sorry sir, I'm unable to confirm the appointment right now. "
    "Please try again in a few minutes."
)


class BookingCalendar(Protocol):
    async def get_available_slots(
        self,
        *,
        start: date,
        end: date,
        request_id: str | None = None,
    ) -> list[object]:
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
    booking_fingerprint: str | None = None
    fulfillment_language: str | None = None
