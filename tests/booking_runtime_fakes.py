from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from voice_agent.config import CalComConfig
from voice_agent.booking.entities import BookingField
from voice_agent.providers.calcom import (
    AvailableSlot,
    CalComBookingConfirmation,
    CalComBookingRequest,
)
from voice_agent.session_memory import CallSessionMemory


def calcom_config() -> CalComConfig:
    return CalComConfig(
        api_key="cal-key",
        base_url="https://api.cal.com/v2",
        slots_api_version="2024-09-04",
        bookings_api_version="2026-02-25",
        event_type_id=123,
        event_type_slug=None,
        username=None,
        team_slug=None,
        organization_slug=None,
        time_zone="Asia/Kolkata",
        duration_minutes=30,
        timeout_seconds=4.0,
        retry_attempts=0,
        default_attendee_email="reception@example.com",
    )


class FakeBookingRuntimeStore:
    def __init__(self, *, fail_persist: bool = False) -> None:
        self.records: dict[str, SimpleNamespace] = {}
        self.fail_persist = fail_persist
        self.reservation_count = 0
        self.persist_count = 0

    async def find_booking_by_fingerprint(self, fingerprint: str) -> object | None:
        return self.records.get(fingerprint)

    async def reserve_booking(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        request_id: str | None = None,
    ) -> bool:
        if fingerprint in self.records:
            return False
        self.reservation_count += 1
        booking_values = memory.booking_values()
        self.records[fingerprint] = SimpleNamespace(
            booking_fingerprint=fingerprint,
            call_id=memory.session_id,
            customer_name=booking_values.get(BookingField.CUSTOMER_NAME),
            phone_number=booking_values.get(BookingField.PHONE_NUMBER),
            calcom_uid=None,
            external_status=None,
            booking_time=None,
            booking_validation_state="reserved",
            confirmation_status="booking_in_progress",
        )
        return True

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
        self.persist_count += 1
        if self.fail_persist:
            return False
        record = self.records[fingerprint]
        record.calcom_uid = calcom_uid
        record.external_status = external_status
        record.booking_time = booking_time
        record.booking_validation_state = validation_state
        record.confirmation_status = "confirmed"
        return True


class FakeCalendar:
    def __init__(
        self,
        *,
        slots: tuple[datetime, ...],
        uid: str = "booking_uid_123",
        status: str | None = "accepted",
        fail_create: bool = False,
    ) -> None:
        self.slots = slots
        self.uid = uid
        self.status = status
        self.fail_create = fail_create
        self.created_bookings: list[CalComBookingRequest] = []

    async def get_available_slots(
        self,
        *,
        start,
        end,
        request_id: str | None = None,
    ) -> list[AvailableSlot]:
        return [AvailableSlot(start=slot) for slot in self.slots]

    async def create_booking(
        self,
        booking: CalComBookingRequest,
        *,
        request_id: str | None = None,
    ) -> CalComBookingConfirmation:
        if self.fail_create:
            raise RuntimeError("calcom unavailable")
        self.created_bookings.append(booking)
        return CalComBookingConfirmation(
            uid=self.uid,
            start=booking.start,
            status=self.status,
        )

    async def aclose(self) -> None:
        return None


@dataclass
class FakeNotificationSink:
    events: list[object]

    async def enqueue_booking_confirmation(self, event, request_id: str | None) -> bool:
        self.events.append(event)
        return True


def slot_tomorrow(hour: int, minute: int = 0) -> datetime:
    tz = ZoneInfo("Asia/Kolkata")
    tomorrow = date.today() + timedelta(days=1)
    return datetime.combine(tomorrow, time(hour, minute), tzinfo=tz)
