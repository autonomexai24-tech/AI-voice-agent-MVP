from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from booking_runtime_fakes import (
    FakeBookingRuntimeStore,
    FakeCalendar,
    FakeNotificationSink,
    calcom_config,
    slot_tomorrow,
)
from voice_agent.booking.runtime import (
    BookingRuntimeResult,
    DuplicateBookingGuard,
    build_booking_fingerprint,
)
from voice_agent.config import BusinessConfig
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.language import default_language_snapshot
from voice_agent.session_memory import CallSessionMemory
from voice_agent.validation import RuntimeIntegrityValidator


def test_active_runtime_creates_calcom_booking_persists_and_sends_sms(caplog) -> None:
    asyncio.run(_run_success_test(caplog))


async def _run_success_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    calendar = FakeCalendar(slots=(slot_tomorrow(18),))
    store = FakeBookingRuntimeStore()
    notifications = FakeNotificationSink(events=[])
    flow = _runtime_flow(calendar=calendar, store=store, notifications=notifications)
    memory = CallSessionMemory(session_id="call-success")

    await _collect_booking(flow, memory)
    result = await flow.handle_turn(
        "Yes correct",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-confirm",
    )

    assert result.status == "complete"
    assert result.runtime_result is not None
    assert result.runtime_result.booking_success is True
    assert result.runtime_result.calcom_booking_uid == "booking_uid_123"
    assert memory.booking.confirmation_completed is True
    assert memory.booking.calcom_uid == "booking_uid_123"
    assert len(calendar.created_bookings) == 1
    assert store.persist_count == 1
    assert len(notifications.events) == 1
    events = [record.getMessage() for record in caplog.records]
    assert "booking_started" in events
    assert "booking_validated" in events
    assert "booking_persisted" in events
    assert "booking_confirmed" in events
    assert "booking_sms_sent" in events


def test_duplicate_confirmation_returns_existing_booking_without_rebooking() -> None:
    asyncio.run(_run_duplicate_test())


async def _run_duplicate_test() -> None:
    calendar = FakeCalendar(slots=(slot_tomorrow(18),))
    store = FakeBookingRuntimeStore()
    flow = _runtime_flow(calendar=calendar, store=store)
    memory = CallSessionMemory(session_id="call-duplicate")

    await _collect_booking(flow, memory)
    first = await flow.handle_turn(
        "Yes correct",
        memory=memory,
        language=default_language_snapshot(),
    )
    memory.booking.awaiting_confirmation = True
    memory.booking.confirmation_completed = False
    second = await flow.handle_turn(
        "Yes correct",
        memory=memory,
        language=default_language_snapshot(),
    )

    assert first.runtime_result is not None
    assert second.runtime_result is not None
    assert second.runtime_result.duplicate_detected is True
    assert second.runtime_result.calcom_booking_uid == first.runtime_result.calcom_booking_uid
    assert len(calendar.created_bookings) == 1
    assert store.persist_count == 1


def test_calcom_failure_never_marks_booking_confirmed() -> None:
    asyncio.run(_run_calcom_failure_test())


async def _run_calcom_failure_test() -> None:
    calendar = FakeCalendar(slots=(slot_tomorrow(18),), fail_create=True)
    store = FakeBookingRuntimeStore()
    flow = _runtime_flow(calendar=calendar, store=store)
    memory = CallSessionMemory(session_id="call-calcom-failure")

    await _collect_booking(flow, memory)
    result = await flow.handle_turn(
        "Yes correct",
        memory=memory,
        language=default_language_snapshot(),
    )

    assert result.status == "booking_failed"
    assert result.runtime_result is not None
    assert result.runtime_result.booking_success is False
    assert memory.booking.confirmation_completed is False
    assert "appointment is confirmed" not in (result.response_text or "").lower()


def test_output_validator_blocks_confirmation_claim_without_persistence() -> None:
    result = BookingRuntimeResult(
        booking_success=False,
        calcom_booking_uid="booking_uid_123",
        external_status="accepted",
        booking_time=slot_tomorrow(18),
        validation_status="valid",
        duplicate_detected=False,
        retry_state="external_created_persistence_failed",
        persistence_status="failed",
        booking_fingerprint="fingerprint",
        response_text="Done, your appointment is confirmed for tomorrow at 6 PM.",
        failure_reason="persistence_failed",
    )

    safe = RuntimeIntegrityValidator().safe_response(
        result.response_text or "",
        result=result,
        language="english",
    )

    assert "appointment is confirmed" not in safe.lower()


def test_duplicate_booking_guard_expires_stale_fingerprints() -> None:
    asyncio.run(_run_duplicate_guard_ttl_test())


async def _run_duplicate_guard_ttl_test() -> None:
    guard = DuplicateBookingGuard(ttl_seconds=0.01)
    fingerprint = build_booking_fingerprint(
        call_id="call-ttl",
        appointment_date="2026-05-14",
        appointment_time="18:00",
        caller_phone="+919876543210",
    )

    assert await guard.start(fingerprint) is None
    duplicate = await guard.start(fingerprint)
    assert duplicate is not None
    assert duplicate.duplicate_detected is True
    assert duplicate.retry_state == "duplicate_in_progress"

    await asyncio.sleep(0.02)
    assert await guard.start(fingerprint) is None


async def _collect_booking(
    flow: ConversationalBookingFlow,
    memory: CallSessionMemory,
) -> None:
    for text in (
        "I need dental cleaning tomorrow at 6 PM",
        "Rahul",
        "9876543210",
        "Any doctor is fine",
        "No notes",
    ):
        await flow.handle_turn(
            text,
            memory=memory,
            language=default_language_snapshot(),
        )


def _runtime_flow(
    *,
    calendar: FakeCalendar,
    store: FakeBookingRuntimeStore,
    notifications: FakeNotificationSink | None = None,
) -> ConversationalBookingFlow:
    return ConversationalBookingFlow(
        _business_config(),
        calcom_config=calcom_config(),
        calendar=calendar,
        persistence_sink=store,
        notification_sink=notifications,
    )


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(),
        receptionist_tone="calm",
        refusal_behavior="Clinic questions only.",
        receptionist_personality="helpful",
        context_path=None,
    )
