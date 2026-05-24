from __future__ import annotations

from voice_agent.config import BusinessConfig, BusinessFAQ
from dataclasses import replace

from voice_agent.language import default_language_snapshot
from voice_agent.realtime_prompt_manager import PromptIntent, RealtimePromptManager
from voice_agent.session_memory import CallSessionMemory


def test_realtime_prompt_injects_runtime_memory_without_transcript_history() -> None:
    memory = CallSessionMemory(session_id="call-1")
    memory.record_turn(role="caller", text="This old transcript should not be injected.")
    memory.capture_booking_fields(
        customer_name="Ravi Kumar",
        service_type="dental cleaning",
    )

    prompt = RealtimePromptManager().compose(
        transcript="Can I talk to the doctor?",
        business=_business_config(),
        memory=memory,
        language=default_language_snapshot(),
        intent=PromptIntent(classification="business_context"),
        request_id="req-1",
    )

    assert "caller_name: Ravi Kumar" in prompt.instructions
    assert "booking_stage:" in prompt.instructions
    assert "pending:" in prompt.instructions
    assert "dental cleaning" in prompt.instructions
    assert "This old transcript should not be injected" not in prompt.instructions
    assert prompt.memory_chars > 0
    assert prompt.recomposition_index == 1


def test_realtime_prompt_selects_only_relevant_faqs_and_stays_compact() -> None:
    manager = RealtimePromptManager(max_prompt_chars=1500, top_faqs=2)
    prompt = manager.compose(
        transcript="What are your opening timings?",
        business=_business_config(),
        memory=CallSessionMemory(session_id="call-2"),
        language=default_language_snapshot(),
        intent=PromptIntent(classification="business_context"),
    )

    assert prompt.faq_injection_count == 1
    assert prompt.selected_faq_questions == ("What are your hours?",)
    assert "10 AM to 7 PM" in prompt.instructions
    assert "Parking is available" not in prompt.instructions
    assert "WiFi password" not in prompt.instructions
    assert 500 <= prompt.prompt_chars <= 1500


def test_realtime_prompt_recomposes_each_turn_with_current_language_and_memory() -> None:
    manager = RealtimePromptManager()
    memory = CallSessionMemory(session_id="call-3")
    first = manager.compose(
        transcript="Can I visit today?",
        business=_business_config(),
        memory=memory,
        language=default_language_snapshot(),
        intent=PromptIntent(classification="business_context"),
    )

    memory.capture_booking_fields(phone_number="9876543210")
    language = replace(
        default_language_snapshot(),
        active_language="hinglish",
        dominant_language="hindi",
        confidence=0.88,
        generation=1,
        openai_response_language="Hinglish",
    )
    second = manager.compose(
        transcript="haan appointment chahiye",
        business=_business_config(),
        memory=memory,
        language=language,
        intent=PromptIntent(classification="business_context"),
    )

    assert second.recomposition_index == first.recomposition_index + 1
    assert "phone_number=9876543210" in second.instructions
    assert "phone_number=9876543210" not in first.instructions
    assert "Respond in natural Hinglish" in second.instructions
    assert "Caller said: haan appointment chahiye" in second.input_text


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=(
            "dental cleaning",
            "braces treatment",
            "root canal",
            "tooth extraction",
        ),
        faqs=(
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
            BusinessFAQ(
                question="Is parking available?",
                answer="Parking is available behind the clinic building.",
            ),
            BusinessFAQ(
                question="What is the WiFi password?",
                answer="WiFi password is shared only inside the clinic.",
            ),
        ),
        receptionist_tone="warm and concise",
        refusal_behavior="Sorry sir, I can help only with {business_type}-related questions.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )
