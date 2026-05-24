from __future__ import annotations

import asyncio
import logging

from dataclasses import replace

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.language import default_language_snapshot
from voice_agent.orchestration import (
    ConversationOrchestrator,
    ConversationRuntimeState,
    IntentRoute,
)
from voice_agent.session_memory import CallSessionMemory
from booking_runtime_fakes import (
    FakeBookingRuntimeStore,
    FakeCalendar,
    calcom_config,
    slot_tomorrow,
)


def test_booking_progression_is_runtime_owned() -> None:
    asyncio.run(_run_booking_progression())


async def _run_booking_progression() -> None:
    memory = CallSessionMemory(session_id="orch-booking")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    first = await orchestrator.handle_turn(
        "I need dental cleaning tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-1",
    )
    second = await orchestrator.handle_turn(
        "Ravi Kumar",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-2",
    )

    assert first.route == IntentRoute.BOOKING
    assert first.handled is True
    assert first.should_call_model is False
    assert first.current_state == ConversationRuntimeState.BOOKING_ACTIVE
    assert first.response_text == "Sure. May I have your name?"
    assert second.response_text == "And your phone number, please?"
    assert memory.booking.selected_service == "dental cleaning"
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time == "evening"


def test_interruption_recovery_returns_to_booking_without_losing_state(caplog) -> None:
    asyncio.run(_run_interruption_recovery(caplog))


async def _run_interruption_recovery(caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = CallSessionMemory(session_id="orch-interrupt")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    await orchestrator.handle_turn(
        "I need braces treatment tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
    )
    interrupted = await orchestrator.handle_turn(
        "wait one second",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-interrupt",
    )

    assert interrupted.route == IntentRoute.INTERRUPTION
    assert interrupted.current_state == ConversationRuntimeState.BOOKING_ACTIVE
    assert memory.booking.selected_service == "braces treatment"
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time == "evening"
    events = [record.getMessage() for record in caplog.records]
    assert "interruption_recovery" in events


def test_retry_policy_escalates_after_repeated_unclear_input() -> None:
    asyncio.run(_run_retry_policy_escalation())


async def _run_retry_policy_escalation() -> None:
    memory = CallSessionMemory(session_id="orch-retry")
    orchestrator = ConversationOrchestrator(
        _business_config(),
        session_id=memory.session_id,
        max_retries_per_key=2,
    )

    first = await orchestrator.handle_turn(
        "noise",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-noise-1",
    )
    second = await orchestrator.handle_turn(
        "unclear",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-noise-2",
    )

    assert first.route == IntentRoute.UNCLEAR
    assert first.retry is not None
    assert first.retry.count == 1
    assert second.route == IntentRoute.ESCALATION
    assert second.current_state == ConversationRuntimeState.ESCALATION_PENDING
    assert memory.escalation_triggered is True


def test_escalation_request_is_runtime_controlled() -> None:
    asyncio.run(_run_escalation_request())


async def _run_escalation_request() -> None:
    memory = CallSessionMemory(session_id="orch-escalation")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    decision = await orchestrator.handle_turn(
        "I want to speak to a human",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-human",
    )

    assert decision.route == IntentRoute.ESCALATION
    assert decision.handled is True
    assert decision.should_call_model is False
    assert memory.escalation_reason == "caller_escalation_request"


def test_multilingual_continuity_is_preserved_on_booking_retry() -> None:
    asyncio.run(_run_multilingual_continuity())


async def _run_multilingual_continuity() -> None:
    memory = CallSessionMemory(session_id="orch-language")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)
    language = replace(
        default_language_snapshot(),
        active_language="hinglish",
        dominant_language="hindi",
        previous_language="english",
        confidence=0.9,
        generation=1,
        openai_response_language="Hinglish",
    )

    decision = await orchestrator.handle_turn(
        "mujhe braces treatment kal shaam chahiye",
        memory=memory,
        language=language,
        request_id="req-hi",
    )

    assert decision.route == IntentRoute.BOOKING
    assert memory.language == "hinglish"
    assert memory.runtime_memory.language.active_language == "hinglish"
    assert decision.response_text == "Sure. Aapka naam bata dijiye?"


def test_correction_handler_keeps_known_booking_fields() -> None:
    asyncio.run(_run_correction_handler())


async def _run_correction_handler() -> None:
    memory = CallSessionMemory(session_id="orch-correction")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    for text in (
        "I need braces treatment tomorrow evening",
        "Rahul",
        "9876543210",
        "Any doctor is fine",
        "No notes",
    ):
        await orchestrator.handle_turn(
            text,
            memory=memory,
            language=default_language_snapshot(),
        )

    corrected = await orchestrator.handle_turn(
        "Actually Friday evening instead",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-correct",
    )

    assert corrected.route == IntentRoute.CORRECTION
    assert corrected.current_state == ConversationRuntimeState.BOOKING_CONFIRMATION
    assert memory.booking.caller_name == "Rahul"
    assert memory.booking.phone_number == "+919876543210"
    assert memory.booking.preferred_date == "friday"
    assert corrected.booking_result is not None
    assert corrected.booking_result.corrected_fields == ("appointment_date",)


def test_invalid_transition_is_blocked_and_observable(caplog) -> None:
    caplog.set_level(logging.INFO)
    orchestrator = ConversationOrchestrator(
        _business_config(),
        session_id="orch-invalid",
        initial_state=ConversationRuntimeState.CALL_ENDING,
    )

    assert orchestrator.transition(
        ConversationRuntimeState.BOOKING_ACTIVE,
        reason="late_booking",
        request_id="req-invalid",
    ) is False

    snapshot = orchestrator.snapshot()
    assert ConversationRuntimeState.BOOKING_ACTIVE in snapshot.blocked_transitions
    assert "state_transition" in [record.getMessage() for record in caplog.records]


def test_faq_can_temporarily_interrupt_booking_without_resetting_workflow() -> None:
    asyncio.run(_run_faq_booking_switch())


async def _run_faq_booking_switch() -> None:
    memory = CallSessionMemory(session_id="orch-faq-switch")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    await orchestrator.handle_turn(
        "I need dental cleaning tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
    )
    faq = await orchestrator.handle_turn(
        "What are your hours?",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-faq",
    )
    resumed = await orchestrator.handle_turn(
        "Ravi Kumar",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-resume",
    )

    assert faq.route == IntentRoute.FAQ
    assert faq.current_state == ConversationRuntimeState.FAQ_RESPONSE
    assert "10 AM to 7 PM" in faq.response_text
    assert memory.booking.selected_service == "dental cleaning"
    assert resumed.route == IntentRoute.BOOKING
    assert resumed.response_text == "And your phone number, please?"


def test_long_call_keeps_orchestration_state_bounded() -> None:
    asyncio.run(_run_long_call_state())


async def _run_long_call_state() -> None:
    memory = CallSessionMemory(session_id="orch-long")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    for index in range(24):
        decision = await orchestrator.handle_turn(
            "What are your hours?",
            memory=memory,
            language=default_language_snapshot(),
            request_id=f"req-long-{index}",
        )
        assert decision.route == IntentRoute.FAQ

    snapshot = orchestrator.snapshot()
    assert snapshot.current_state == ConversationRuntimeState.FAQ_RESPONSE
    assert len(snapshot.retry_counts) == 0
    assert decision.latency_ms < 80


def test_state_persistence_snapshot_exposes_recovery_and_allowed_transitions() -> None:
    asyncio.run(_run_state_persistence_snapshot())


async def _run_state_persistence_snapshot() -> None:
    memory = CallSessionMemory(session_id="orch-snapshot")
    orchestrator = ConversationOrchestrator(_business_config(), session_id=memory.session_id)

    await orchestrator.handle_turn(
        "I need root canal tomorrow morning",
        memory=memory,
        language=default_language_snapshot(),
    )
    await orchestrator.handle_turn(
        "wait",
        memory=memory,
        language=default_language_snapshot(),
    )

    snapshot = orchestrator.snapshot()
    assert snapshot.current_state == ConversationRuntimeState.BOOKING_ACTIVE
    assert snapshot.recovery_state == ConversationRuntimeState.BOOKING_ACTIVE
    assert ConversationRuntimeState.FAQ_RESPONSE in snapshot.allowed_transitions
    assert memory.snapshot()["runtime_memory"]["turn_index"] >= 0


def test_confirmed_booking_can_move_to_call_ending() -> None:
    asyncio.run(_run_call_ending())


async def _run_call_ending() -> None:
    memory = CallSessionMemory(session_id="orch-ending")
    orchestrator = ConversationOrchestrator(
        _business_config(),
        session_id=memory.session_id,
        calcom_config=calcom_config(),
        booking_calendar=FakeCalendar(slots=(slot_tomorrow(17),)),
        persistence_sink=FakeBookingRuntimeStore(),
    )

    for text in (
        "I need dental cleaning tomorrow evening",
        "Rahul",
        "9876543210",
        "Any doctor is fine",
        "No notes",
        "Yes correct",
    ):
        await orchestrator.handle_turn(
            text,
            memory=memory,
            language=default_language_snapshot(),
        )

    ending = await orchestrator.handle_turn(
        "thank you bye",
        memory=memory,
        language=default_language_snapshot(),
    )

    assert ending.route == IntentRoute.CALL_ENDING
    assert ending.current_state == ConversationRuntimeState.CALL_ENDING
    assert ending.should_call_model is False


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
            BusinessFAQ(
                question="Where are you located?",
                answer="We are on MG Road, near the metro station.",
            ),
        ),
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm",
        context_path=None,
    )
