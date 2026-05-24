from __future__ import annotations

import asyncio
from dataclasses import replace

from voice_agent.config import BusinessConfig
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.realtime_prompt_manager import PromptIntent, RealtimePromptManager
from voice_agent.session_memory import CallSessionMemory


def test_runtime_memory_compresses_long_calls_without_raw_transcript_dump() -> None:
    memory = CallSessionMemory(session_id="long-call")
    raw_turn = "Caller gave a long unrelated story that should never be dumped verbatim."

    for index in range(20):
        memory.record_turn(role="caller", text=f"{raw_turn} turn {index}")
        memory.record_turn(role="assistant", text="What phone number should I use?")

    snapshot = memory.runtime_memory.snapshot()
    injection = memory.runtime_memory.build_injection()

    assert snapshot.turn_index == 40
    assert snapshot.compressed_turns >= 16
    assert len(injection) <= 760
    assert raw_turn not in injection
    assert "phone_number" in injection
    assert "ask_policy" in injection


def test_runtime_memory_resolves_repeated_questions_after_field_capture() -> None:
    memory = CallSessionMemory(session_id="repeat-question")
    memory.runtime_memory.mark_unresolved_question(field_name="phone_number")
    memory.runtime_memory.mark_unresolved_question(field_name="phone_number")

    unresolved = memory.runtime_memory.unresolved_questions()
    assert unresolved[0].field_name == "phone_number"
    assert unresolved[0].attempts == 2

    memory.capture_booking_fields(phone_number="9876543210")

    assert all(
        question.field_name != "phone_number"
        for question in memory.runtime_memory.unresolved_questions()
    )


def test_runtime_memory_tracks_booking_corrections_deterministically() -> None:
    asyncio.run(_run_booking_correction_memory_test())


async def _run_booking_correction_memory_test() -> None:
    memory = CallSessionMemory(session_id="memory-correction")
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
    )

    snapshot = memory.runtime_memory.snapshot()
    assert corrected.corrected_fields == ("appointment_date",)
    assert snapshot.booking.values["appointment_date"] == "friday"
    assert snapshot.correction.active_fields == ("appointment_date",)
    assert snapshot.booking.awaiting_confirmation is True


def test_runtime_memory_preserves_multilingual_continuity() -> None:
    asyncio.run(_run_multilingual_memory_test())


async def _run_multilingual_memory_test() -> None:
    router = SessionLanguageRouter(initial_language="english")
    language = (
        await router.route_text(
            "mujhe braces treatment kal shaam chahiye",
            request_id="req-hi",
            is_final=True,
        )
    ).snapshot
    memory = CallSessionMemory(session_id="memory-language")
    memory.runtime_memory.update_language(language)

    injection = memory.runtime_memory.build_injection()

    assert "active=hinglish" in injection
    assert "previous=english" in injection
    assert memory.runtime_memory.snapshot().language.switch_count == 1


def test_runtime_memory_preserves_escalation_continuity() -> None:
    memory = CallSessionMemory(session_id="memory-escalation")
    memory.mark_escalation(reason="caller asked for human supervisor")

    snapshot = memory.runtime_memory.snapshot()
    injection = memory.runtime_memory.build_injection()

    assert snapshot.escalation.triggered is True
    assert snapshot.escalation.reason == "caller asked for human supervisor"
    assert "escalation: triggered:caller asked for human supervisor" in injection


def test_prompt_manager_uses_runtime_memory_for_compact_injection() -> None:
    memory = CallSessionMemory(session_id="prompt-runtime-memory")
    memory.capture_booking_fields(
        customer_name="Ravi",
        service_type="dental cleaning",
    )
    language = replace(
        default_language_snapshot(),
        active_language="hinglish",
        dominant_language="hindi",
        previous_language="english",
        generation=2,
        openai_response_language="Hinglish",
    )

    prompt = RealtimePromptManager(max_prompt_chars=1500).compose(
        transcript="kal shaam appointment chahiye",
        business=_business_config(),
        memory=memory,
        language=language,
        intent=PromptIntent(classification="booking"),
    )

    assert "Runtime memory:" in prompt.instructions
    assert "caller_name: Ravi" in prompt.instructions
    assert "booking_stage:" in prompt.instructions
    assert "ask_policy" in prompt.instructions
    assert prompt.memory_chars <= 760
    assert prompt.prompt_chars <= 1500


def test_runtime_memory_survives_interruption_recovery_generation_changes() -> None:
    memory = CallSessionMemory(session_id="memory-interruption")
    memory.capture_booking_fields(customer_name="Ravi", service_type="root canal")
    first_language = replace(default_language_snapshot(), generation=1)
    second_language = replace(
        default_language_snapshot(),
        active_language="hinglish",
        dominant_language="hindi",
        previous_language="english",
        generation=2,
        openai_response_language="Hinglish",
    )

    memory.runtime_memory.update_language(first_language)
    memory.runtime_memory.mark_unresolved_question(field_name="appointment_time")
    memory.runtime_memory.update_language(second_language)

    snapshot = memory.runtime_memory.snapshot()
    assert snapshot.booking.values["customer_name"] == "Ravi"
    assert snapshot.booking.values["service_type"] == "root canal"
    assert snapshot.language.generation == 2
    assert snapshot.unresolved_questions[0].field_name == "appointment_time"


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
