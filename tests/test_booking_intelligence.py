from __future__ import annotations

import asyncio
import logging

from voice_agent.booking.entities import BookingField
from voice_agent.booking.extraction import ExtractionContext, extract_booking_entities
from voice_agent.config import BusinessConfig
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.session_memory import CallSessionMemory
from booking_runtime_fakes import (
    FakeBookingRuntimeStore,
    FakeCalendar,
    calcom_config,
    slot_tomorrow,
)


def test_structured_extraction_collects_incremental_booking_entities() -> None:
    extraction = extract_booking_entities(
        "I need braces treatment tomorrow evening.",
        ExtractionContext(
            pending_fields=(
                "customer_name",
                "phone_number",
                "service_type",
                "appointment_date",
                "appointment_time",
                "doctor_preference",
                "notes",
            ),
            services=("dental cleaning", "braces treatment"),
            language="english",
            known_values={},
        ),
    )

    fields = extraction.by_field()
    assert fields[BookingField.SERVICE_TYPE].value == "braces treatment"
    assert fields[BookingField.APPOINTMENT_DATE].value == "tomorrow"
    assert fields[BookingField.APPOINTMENT_TIME].value == "evening"


def test_day_after_tomorrow_is_not_treated_as_conflicting_date() -> None:
    extraction = extract_booking_entities(
        "I need dental cleaning day after tomorrow morning.",
        ExtractionContext(
            pending_fields=(
                "customer_name",
                "phone_number",
                "service_type",
                "appointment_date",
                "appointment_time",
                "doctor_preference",
                "notes",
            ),
            services=("dental cleaning", "braces treatment"),
            language="english",
            known_values={},
        ),
    )

    fields = extraction.by_field()
    assert fields[BookingField.APPOINTMENT_DATE].value == "day after tomorrow"
    assert not [issue for issue in extraction.issues if issue.code == "conflicting_date_values"]


def test_indic_script_date_time_preserves_multilingual_booking_continuity() -> None:
    asyncio.run(_run_indic_script_multilingual_test())


async def _run_indic_script_multilingual_test() -> None:
    router = SessionLanguageRouter(initial_language="english")
    language = (
        await router.route_text(
            "mujhe braces treatment कल शाम चाहिए",
            request_id="req-devanagari",
            is_final=True,
        )
    ).snapshot
    memory = CallSessionMemory(session_id="booking-devanagari")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "mujhe braces treatment कल शाम चाहिए",
        memory=memory,
        language=language,
        request_id="req-devanagari",
    )

    assert result.handled is True
    assert memory.booking.selected_service == "braces treatment"
    assert memory.booking.preferred_date == "kal"
    assert memory.booking.preferred_time == "evening"
    assert result.response_text == "Sure. Aapka naam bata dijiye?"


def test_customer_name_can_be_extracted_from_full_booking_sentence() -> None:
    extraction = extract_booking_entities(
        "Dental cleaning tomorrow at 6 PM for Rahul.",
        ExtractionContext(
            pending_fields=(
                "customer_name",
                "phone_number",
                "service_type",
                "appointment_date",
                "appointment_time",
                "doctor_preference",
                "notes",
            ),
            services=("dental cleaning", "braces treatment"),
            language="english",
            known_values={},
        ),
    )

    fields = extraction.by_field()
    assert fields[BookingField.CUSTOMER_NAME].value == "Rahul"
    assert fields[BookingField.SERVICE_TYPE].value == "dental cleaning"


def test_booking_memory_exposes_typed_inspectable_snapshot() -> None:
    memory = CallSessionMemory(session_id="booking-snapshot")
    memory.capture_booking_fields(
        customer_name="Rahul",
        service_type="dental cleaning",
        appointment_date="tomorrow",
    )

    snapshot = memory.booking_state_snapshot()

    assert snapshot.values[BookingField.CUSTOMER_NAME] == "Rahul"
    assert snapshot.pending_required_fields == (
        BookingField.PHONE_NUMBER,
        BookingField.APPOINTMENT_TIME,
        BookingField.DOCTOR_PREFERENCE,
    )
    assert snapshot.pending_optional_fields == (BookingField.NOTES,)


def test_non_booking_need_statement_is_left_for_business_prompt() -> None:
    asyncio.run(_run_non_booking_need_test())


async def _run_non_booking_need_test() -> None:
    memory = CallSessionMemory(session_id="booking-non-booking")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "I need your clinic hours",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-hours",
    )

    assert result.handled is False
    assert memory.booking.selected_service is None


def test_invalid_time_preserves_valid_fields_and_asks_natural_retry(caplog) -> None:
    asyncio.run(_run_invalid_time_test(caplog))


async def _run_invalid_time_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = CallSessionMemory(session_id="booking-invalid-time")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "I need dental cleaning tomorrow at 32 PM",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-invalid-time",
    )

    assert result.handled is True
    assert "appointment time" in result.response_text
    assert memory.booking.selected_service == "dental cleaning"
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time is None
    events = [record.getMessage() for record in caplog.records]
    assert "booking_validation_failed" in events
    assert "booking_retry_triggered" in events


def test_unsupported_service_is_rejected_without_capturing_fake_booking() -> None:
    asyncio.run(_run_unsupported_service_test())


async def _run_unsupported_service_test() -> None:
    memory = CallSessionMemory(session_id="booking-unsupported")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "I need hair spa tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-unsupported",
    )

    assert "service" in result.response_text.lower()
    assert memory.booking.selected_service is None
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time == "evening"


def test_missing_field_recovery_asks_only_for_missing_time() -> None:
    asyncio.run(_run_missing_field_test())


async def _run_missing_field_test() -> None:
    memory = CallSessionMemory(session_id="booking-missing")
    memory.capture_booking_fields(
        customer_name="Rahul",
        phone_number="9876543210",
        service_type="dental cleaning",
        appointment_date="tomorrow",
        doctor_preference="any doctor",
        optional_notes="",
    )
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "appointment",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-missing",
    )

    assert result.response_text == "What time would you prefer?"
    assert result.pending_fields == ("appointment_time",)


def test_confirmation_and_completion_are_deterministic() -> None:
    asyncio.run(_run_confirmation_test())


async def _run_confirmation_test() -> None:
    memory = CallSessionMemory(session_id="booking-confirmation")
    flow = ConversationalBookingFlow(
        _business_config(),
        calcom_config=calcom_config(),
        calendar=FakeCalendar(slots=(slot_tomorrow(18),)),
        persistence_sink=FakeBookingRuntimeStore(),
    )

    await flow.handle_turn(
        "I need dental cleaning tomorrow at 6 PM",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-1",
    )
    await flow.handle_turn("Rahul", memory=memory, language=default_language_snapshot())
    await flow.handle_turn("9876543210", memory=memory, language=default_language_snapshot())
    await flow.handle_turn("Any doctor is fine", memory=memory, language=default_language_snapshot())
    summary = await flow.handle_turn("No notes", memory=memory, language=default_language_snapshot())
    completed = await flow.handle_turn("Yes, correct", memory=memory, language=default_language_snapshot())
    followup = await flow.handle_turn("thank you", memory=memory, language=default_language_snapshot())

    assert summary.status == "awaiting_confirmation"
    assert summary.response_text == (
        "Just confirming: Dental cleaning tomorrow at 6 PM with any doctor for Rahul. "
        "Is that correct?"
    )
    assert completed.status == "complete"
    assert memory.booking.confirmation_completed is True
    assert followup.handled is False


def test_correction_updates_only_changed_fields_after_summary() -> None:
    asyncio.run(_run_correction_test())


async def _run_correction_test() -> None:
    memory = CallSessionMemory(session_id="booking-correction")
    flow = ConversationalBookingFlow(_business_config())

    await flow.handle_turn(
        "I need braces treatment tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
    )
    await flow.handle_turn("Rahul", memory=memory, language=default_language_snapshot())
    await flow.handle_turn("9876543210", memory=memory, language=default_language_snapshot())
    await flow.handle_turn("Any doctor is fine", memory=memory, language=default_language_snapshot())
    await flow.handle_turn("No notes", memory=memory, language=default_language_snapshot())
    corrected = await flow.handle_turn(
        "Actually Friday evening instead.",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-correct",
    )

    assert corrected.corrected_fields == ("appointment_date",)
    assert memory.booking.caller_name == "Rahul"
    assert memory.booking.phone_number == "+919876543210"
    assert memory.booking.selected_service == "braces treatment"
    assert memory.booking.preferred_date == "friday"
    assert memory.booking.preferred_time == "evening"
    assert corrected.status == "awaiting_confirmation"
    assert "Friday" in corrected.response_text


def test_booking_continuity_survives_silence_without_reset(caplog) -> None:
    asyncio.run(_run_silence_continuity_test(caplog))


async def _run_silence_continuity_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = CallSessionMemory(session_id="booking-silence")
    flow = ConversationalBookingFlow(_business_config())

    await flow.handle_turn(
        "I need dental cleaning tomorrow",
        memory=memory,
        language=default_language_snapshot(),
    )
    silence = await flow.handle_turn("", memory=memory, language=default_language_snapshot())

    assert silence.handled is True
    assert memory.booking.selected_service == "dental cleaning"
    assert memory.booking.preferred_date == "tomorrow"
    assert "No hurry" in silence.response_text
    events = [record.getMessage() for record in caplog.records]
    assert "booking_continuity_preserved" in events


def test_multilingual_booking_continuity_extracts_code_mixed_details() -> None:
    asyncio.run(_run_multilingual_test())


async def _run_multilingual_test() -> None:
    router = SessionLanguageRouter(initial_language="english")
    language = (
        await router.route_text(
            "mujhe braces treatment kal shaam chahiye",
            request_id="req-hi",
            is_final=True,
        )
    ).snapshot
    memory = CallSessionMemory(session_id="booking-multilingual")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "mujhe braces treatment kal shaam chahiye",
        memory=memory,
        language=language,
        request_id="req-hi",
    )

    assert language.active_language == "hinglish"
    assert memory.booking.language == "hinglish"
    assert memory.booking.selected_service == "braces treatment"
    assert memory.booking.preferred_date == "kal"
    assert memory.booking.preferred_time == "evening"
    assert result.response_text == "Sure. Aapka naam bata dijiye?"


def test_low_confidence_time_asks_clarification_without_fake_confirmation(caplog) -> None:
    asyncio.run(_run_low_confidence_test(caplog))


async def _run_low_confidence_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = CallSessionMemory(session_id="booking-low-confidence")
    flow = ConversationalBookingFlow(_business_config())

    result = await flow.handle_turn(
        "Book dental cleaning tomorrow at 6 baje",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-low",
    )

    assert "AM or PM" in result.response_text
    assert memory.booking.preferred_time is None
    assert memory.booking.awaiting_confirmation is False
    events = [record.getMessage() for record in caplog.records]
    assert "booking_confidence_low" in events


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(),
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm",
        context_path=None,
    )
