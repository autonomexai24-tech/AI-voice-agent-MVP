from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from voice_agent.booking import BookingConfirmationEvent
from voice_agent.notifications import (
    BookingConfirmationNotificationOrchestrator,
    build_booking_confirmation_sms,
)


def test_booking_confirmation_sms_is_short_and_includes_required_fields() -> None:
    event = _event()

    message = build_booking_confirmation_sms(event)

    assert message == (
        "Hello Ravi Kumar, your appointment at Smile Dental Clinic "
        "is confirmed for 14 May 2026 at 7:30 PM."
    )
    assert len(message) < 120


def test_notification_orchestrator_dedupes_and_contains_sms_failures() -> None:
    asyncio.run(_run_notification_failure_test())


async def _run_notification_failure_test() -> None:
    provider = _FailingSMSProvider()
    orchestrator = BookingConfirmationNotificationOrchestrator(
        sms_provider=provider,
        queue_max_items=10,
        drain_timeout_seconds=1.0,
    )

    queued = await orchestrator.enqueue_booking_confirmation(_event(), "req-1")
    duplicate = await orchestrator.enqueue_booking_confirmation(_event(), "req-2")
    await orchestrator.aclose()

    assert queued is True
    assert duplicate is False
    assert provider.messages == [
        "Hello Ravi Kumar, your appointment at Smile Dental Clinic "
        "is confirmed for 14 May 2026 at 7:30 PM."
    ]
    assert provider.phone_numbers == ["+919876543210"]


def _event() -> BookingConfirmationEvent:
    return BookingConfirmationEvent(
        booking_uid="booking_uid_123",
        booking_status="accepted",
        customer_name="Ravi Kumar",
        phone_number="+919876543210",
        business_name="Smile Dental Clinic",
        appointment_start=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc),
        time_zone="Asia/Kolkata",
    )


class _FailingSMSProvider:
    def __init__(self) -> None:
        self.phone_numbers: list[str] = []
        self.messages: list[str] = []

    async def send_sms(
        self,
        *,
        phone_number: str,
        message: str,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        self.phone_numbers.append(phone_number)
        self.messages.append(message)
        raise RuntimeError("provider failed")
