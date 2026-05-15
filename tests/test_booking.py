from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import httpx

from voice_agent.booking import BookingConfirmationEvent, BookingWorkflowOrchestrator
from voice_agent.config import BusinessConfig, CalComConfig
from voice_agent.providers.calcom import (
    AvailableSlot,
    CalComBookingConfirmation,
    CalComBookingRequest,
    CalComClient,
)


def test_booking_workflow_collects_fields_and_creates_exact_booking() -> None:
    asyncio.run(_run_exact_booking_flow())


async def _run_exact_booking_flow() -> None:
    calendar = _FakeCalendar(
        slots=[AvailableSlot(start=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc))]
    )
    workflow = _workflow(calendar)

    first = await workflow.handle_turn(
        "I want to book an appointment tomorrow at 2 PM",
        request_id="req-1",
    )
    second = await workflow.handle_turn("My name is Ravi Kumar", request_id="req-2")
    third = await workflow.handle_turn("9876543210", request_id="req-3")

    assert first.response_text == "May I have your name, sir?"
    assert second.response_text == "And your phone number, please?"
    assert third.status == "confirmed"
    assert third.response_text == "Done sir, your appointment is confirmed for tomorrow at 2 PM."
    assert calendar.availability_checks == [(date(2026, 5, 14), date(2026, 5, 16))]
    assert len(calendar.created_bookings) == 1
    assert calendar.created_bookings[0].attendee_name == "Ravi Kumar"
    assert calendar.created_bookings[0].attendee_phone == "+919876543210"
    assert calendar.created_bookings[0].attendee_email == "reception@example.com"


def test_booking_workflow_suggests_alternative_then_books_after_confirmation() -> None:
    asyncio.run(_run_alternative_booking_flow())


async def _run_alternative_booking_flow() -> None:
    calendar = _FakeCalendar(
        slots=[AvailableSlot(start=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc))]
    )
    workflow = _workflow(calendar)

    await workflow.handle_turn("Book appointment tomorrow morning", request_id="req-1")
    await workflow.handle_turn("Anita Shah", request_id="req-2")
    unavailable = await workflow.handle_turn("phone number is 9876543210", request_id="req-3")
    confirmed = await workflow.handle_turn("Yes, that works", request_id="req-4")

    assert unavailable.status == "awaiting_slot_confirmation"
    assert unavailable.response_text == (
        "tomorrow morning is full sir. We have tomorrow at 2 PM available. "
        "Would that work for you?"
    )
    assert confirmed.status == "confirmed"
    assert confirmed.response_text == "Done sir, your appointment is confirmed for tomorrow at 2 PM."
    assert len(calendar.created_bookings) == 1


def test_booking_confirmation_event_emits_once_and_followup_does_not_rebook() -> None:
    asyncio.run(_run_confirmation_event_once_flow())


async def _run_confirmation_event_once_flow() -> None:
    events: list[tuple[BookingConfirmationEvent, str | None]] = []

    async def on_booking_confirmed(
        event: BookingConfirmationEvent,
        request_id: str | None,
    ) -> None:
        events.append((event, request_id))

    calendar = _FakeCalendar(
        slots=[AvailableSlot(start=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc))]
    )
    workflow = _workflow(calendar, on_booking_confirmed=on_booking_confirmed)

    await workflow.handle_turn("Book appointment tomorrow at 2 PM", request_id="req-1")
    await workflow.handle_turn("Ravi Kumar", request_id="req-2")
    confirmed = await workflow.handle_turn("9876543210", request_id="req-3")
    followup = await workflow.handle_turn("thank you", request_id="req-4")

    assert confirmed.status == "confirmed"
    assert followup.handled is False
    assert followup.status == "confirmed"
    assert len(calendar.created_bookings) == 1
    assert len(events) == 1
    event, request_id = events[0]
    assert request_id == "req-3"
    assert event.booking_uid == "booking_uid_123"
    assert event.customer_name == "Ravi Kumar"
    assert event.phone_number == "+919876543210"
    assert event.business_name == "Smile Dental Clinic"
    assert event.appointment_start == datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc)


def test_booking_workflow_returns_natural_failure_when_calcom_not_configured() -> None:
    asyncio.run(_run_missing_calcom_config_flow())


async def _run_missing_calcom_config_flow() -> None:
    workflow = BookingWorkflowOrchestrator(
        config=_calcom_config(api_key=None),
        business_config=_business_config(),
        calendar=_FakeCalendar(),
        now_provider=lambda: datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
    )

    await workflow.handle_turn("Book appointment tomorrow at 2 PM", request_id="req-1")
    await workflow.handle_turn("Ravi Kumar", request_id="req-2")
    result = await workflow.handle_turn("9876543210", request_id="req-3")

    assert result.status == "failed"
    assert result.response_text == (
        "Sorry sir, I'm unable to confirm the appointment right now. "
        "Please try again in a few minutes."
    )


def test_calcom_client_checks_slots_and_creates_booking_with_v2_headers() -> None:
    asyncio.run(_run_calcom_client_http_test())


async def _run_calcom_client_http_test() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/v2/slots"
            assert request.headers["authorization"] == "Bearer cal_test_key"
            assert request.headers["cal-api-version"] == "2024-09-04"
            assert request.url.params["eventTypeId"] == "123"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "2026-05-14": [
                            {"start": "2026-05-14T14:00:00.000+00:00"}
                        ]
                    },
                },
            )
        assert request.method == "POST"
        assert request.url.path == "/v2/bookings"
        assert request.headers["authorization"] == "Bearer cal_test_key"
        assert request.headers["cal-api-version"] == "2026-02-25"
        payload = json.loads(request.content)
        assert payload["eventTypeId"] == 123
        assert payload["start"] == "2026-05-14T14:00:00Z"
        assert payload["attendee"]["name"] == "Ravi Kumar"
        assert payload["attendee"]["phoneNumber"] == "+919876543210"
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "booking_uid_123",
                    "status": "accepted",
                    "start": "2026-05-14T14:00:00Z",
                },
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.cal.com",
    )
    client = CalComClient(_calcom_config(), http_client=http_client)

    slots = await client.get_available_slots(
        start=date(2026, 5, 14),
        end=date(2026, 5, 16),
        request_id="req-1",
    )
    confirmation = await client.create_booking(
        CalComBookingRequest(
            start=slots[0].start,
            attendee_name="Ravi Kumar",
            attendee_phone="+919876543210",
            attendee_email="reception@example.com",
        ),
        request_id="req-2",
    )
    await http_client.aclose()

    assert len(requests) == 2
    assert slots[0].start == datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc)
    assert confirmation.uid == "booking_uid_123"
    assert confirmation.status == "accepted"


def _workflow(
    calendar: _FakeCalendar,
    on_booking_confirmed=None,
) -> BookingWorkflowOrchestrator:
    return BookingWorkflowOrchestrator(
        config=_calcom_config(),
        business_config=_business_config(),
        calendar=calendar,
        now_provider=lambda: datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
        on_booking_confirmed=on_booking_confirmed,
    )


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental consultation",),
        faqs=(),
        receptionist_tone="warm",
        refusal_behavior="Sorry sir, I can help only with {business_type}-related questions.",
        receptionist_personality="calm",
        context_path=None,
    )


def _calcom_config(api_key: str | None = "cal_test_key") -> CalComConfig:
    return CalComConfig(
        api_key=api_key,
        base_url="https://api.cal.com/v2",
        slots_api_version="2024-09-04",
        bookings_api_version="2026-02-25",
        event_type_id=123,
        event_type_slug=None,
        username=None,
        team_slug=None,
        organization_slug=None,
        time_zone="UTC",
        duration_minutes=30,
        timeout_seconds=3.0,
        retry_attempts=1,
        default_attendee_email="reception@example.com",
    )


class _FakeCalendar:
    def __init__(self, slots: list[AvailableSlot] | None = None) -> None:
        self.slots = slots or []
        self.availability_checks: list[tuple[date, date]] = []
        self.created_bookings: list[CalComBookingRequest] = []

    async def get_available_slots(
        self,
        *,
        start: date,
        end: date,
        request_id: str | None = None,
    ) -> list[AvailableSlot]:
        self.availability_checks.append((start, end))
        return self.slots

    async def create_booking(
        self,
        booking: CalComBookingRequest,
        *,
        request_id: str | None = None,
    ) -> CalComBookingConfirmation:
        self.created_bookings.append(booking)
        return CalComBookingConfirmation(
            uid="booking_uid_123",
            start=booking.start,
            status="accepted",
        )

    async def aclose(self) -> None:
        return None
