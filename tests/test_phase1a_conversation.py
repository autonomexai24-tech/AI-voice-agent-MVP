from __future__ import annotations

import asyncio

from voice_agent.business_prompt import BusinessPromptOrchestrator
from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.prompts.composer import PromptContext, compose_prompt
from voice_agent.session_memory import CallSessionMemory
from booking_runtime_fakes import (
    FakeBookingRuntimeStore,
    FakeCalendar,
    calcom_config,
    slot_tomorrow,
)


def test_prompt_composition_injects_business_and_booking_memory() -> None:
    memory = CallSessionMemory(session_id="room-1")
    memory.capture_booking_fields(
        customer_name="Ravi Kumar",
        service_type="dental cleaning",
        appointment_date="tomorrow",
    )

    prompt = compose_prompt(
        PromptContext(
            business=_business_config(),
            language=default_language_snapshot(),
            booking_memory=memory,
        )
    )

    assert "Business name: Smile Dental Clinic" in prompt.instructions
    assert "Services: dental cleaning; braces treatment; root canal" in prompt.instructions
    assert "Receptionist tone: warm and professional" in prompt.instructions
    assert "Current in-call booking memory" in prompt.instructions
    assert "customer_name=Ravi Kumar" in prompt.instructions
    assert "Pending: phone_number, appointment_time, doctor_preference, notes" in prompt.instructions


def test_refusal_behavior_stays_business_only() -> None:
    async def run() -> None:
        orchestrator = BusinessPromptOrchestrator(_business_config())
        decision = await orchestrator.prepare_response(
            "Who is the PM of India?",
            language=default_language_snapshot(),
            request_id="req-refusal",
        )

        assert decision.classification == "unrelated"
        assert decision.generation_source == "local_guardrail"
        assert decision.response_text == "Sorry, I can help only with clinic bookings and services."

    asyncio.run(run())


def test_conversational_booking_collects_phase1a_fields_in_memory() -> None:
    asyncio.run(_run_booking_collection())


async def _run_booking_collection() -> None:
    memory = CallSessionMemory(session_id="room-123")
    flow = ConversationalBookingFlow(
        _business_config(),
        calcom_config=calcom_config(),
        calendar=FakeCalendar(slots=(slot_tomorrow(10),)),
        persistence_sink=FakeBookingRuntimeStore(),
    )
    language = default_language_snapshot()

    first = await flow.handle_turn(
        "I want to book dental cleaning tomorrow at 10 AM",
        memory=memory,
        language=language,
        request_id="req-1",
    )
    second = await flow.handle_turn(
        "My name is Ravi Kumar",
        memory=memory,
        language=language,
        request_id="req-2",
    )
    third = await flow.handle_turn(
        "9876543210",
        memory=memory,
        language=language,
        request_id="req-3",
    )
    fourth = await flow.handle_turn(
        "Any doctor is fine",
        memory=memory,
        language=language,
        request_id="req-4",
    )
    fifth = await flow.handle_turn(
        "No notes",
        memory=memory,
        language=language,
        request_id="req-5",
    )
    sixth = await flow.handle_turn(
        "Yes, correct",
        memory=memory,
        language=language,
        request_id="req-6",
    )

    assert first.handled is True
    assert first.captured_fields == ("service_type", "appointment_date", "appointment_time")
    assert first.response_text == "Sure. May I have your name?"
    assert second.captured_fields == ("customer_name",)
    assert second.response_text == "And your phone number, please?"
    assert third.captured_fields == ("phone_number",)
    assert third.response_text == "Do you have a doctor preference, or is any doctor okay?"
    assert fourth.captured_fields == ("doctor_preference",)
    assert fourth.response_text == "Any notes I should add for the visit?"
    assert fifth.status == "awaiting_confirmation"
    assert fifth.response_text == (
        "Just confirming: Dental cleaning tomorrow at 10 AM with any doctor for Ravi Kumar. "
        "Is that correct?"
    )
    assert sixth.status == "complete"
    assert memory.booking.caller_name == "Ravi Kumar"
    assert memory.booking.phone_number == "+919876543210"
    assert memory.booking.selected_service == "dental cleaning"
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time == "10 AM"
    assert memory.booking.doctor_preference == "any doctor"
    assert memory.booking.optional_notes == ""
    assert memory.pending_booking_fields == ()


def test_hinglish_booking_response_preserves_caller_style() -> None:
    asyncio.run(_run_hinglish_booking_response())


async def _run_hinglish_booking_response() -> None:
    memory = CallSessionMemory(session_id="room-hi")
    flow = ConversationalBookingFlow(_business_config())
    router = SessionLanguageRouter(initial_language="english")
    language = (
        await router.route_text(
            "mujhe appointment chahiye",
            request_id="req-hi",
            is_final=True,
        )
    ).snapshot

    result = await flow.handle_turn(
        "mujhe appointment chahiye",
        memory=memory,
        language=language,
        request_id="req-hi",
    )

    assert result.handled is True
    assert language.active_language == "hinglish"
    assert result.response_text == "Sure. Aapka naam bata dijiye?"


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(
            BusinessFAQ(
                question="What are your hours?",
                answer="10 AM to 7 PM.",
            ),
        ),
        receptionist_tone="warm and professional",
        refusal_behavior="Sorry, I can help only with clinic bookings and services.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )
