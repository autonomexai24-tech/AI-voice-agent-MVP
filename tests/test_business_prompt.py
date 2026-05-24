from __future__ import annotations

import asyncio

from voice_agent.business_prompt import BusinessPromptOrchestrator
from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.language import default_language_snapshot


def test_business_prompt_answers_supported_service_locally() -> None:
    decision = asyncio.run(_prepare("Do you provide braces?"))

    assert decision.classification == "service_supported"
    assert decision.generation_source == "local_guardrail"
    assert decision.response_text == "Yes sir, we provide braces treatment at Smile Dental Clinic."
    assert decision.uses_model is False


def test_business_prompt_refuses_unrelated_question() -> None:
    decision = asyncio.run(_prepare("Who is the prime minister?"))

    assert decision.classification == "unrelated"
    assert decision.generation_source == "local_guardrail"
    assert decision.response_text == "Sorry sir, I can help only with clinic-related questions."


def test_business_prompt_refuses_unsupported_service_without_hallucinating() -> None:
    decision = asyncio.run(_prepare("Do you provide heart surgery?"))

    assert decision.classification == "unsupported_service"
    assert decision.generation_source == "local_guardrail"
    assert decision.requested_service == "heart surgery"
    assert decision.response_text == "Sorry sir, we do not provide heart surgery at Smile Dental Clinic."


def test_business_prompt_answers_matching_faq() -> None:
    decision = asyncio.run(_prepare("What are your timings?"))

    assert decision.classification == "faq"
    assert decision.generation_source == "openai"
    assert decision.response_text is None
    assert decision.uses_model is True
    assert decision.matched_faq == "What are your hours?"
    assert decision.instructions is not None
    assert "Business name: Smile Dental Clinic" in decision.instructions


def test_business_prompt_builds_model_context_for_business_question() -> None:
    decision = asyncio.run(_prepare("Can I talk to the doctor?"))

    assert decision.classification == "business_context"
    assert decision.generation_source == "openai"
    assert decision.response_text is None
    assert decision.instructions is not None
    assert "Business name: Smile Dental Clinic" in decision.instructions
    assert "Services: braces treatment; dental cleaning" in decision.instructions
    assert "Refuse unrelated topics politely" in decision.instructions
    assert decision.input_text is not None
    assert "Caller said: Can I talk to the doctor?" in decision.input_text


async def _prepare(transcript: str):
    orchestrator = BusinessPromptOrchestrator(_business_config())
    return await orchestrator.prepare_response(
        transcript,
        language=default_language_snapshot(),
        request_id="req-test",
    )


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("braces treatment", "dental cleaning"),
        faqs=(
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
        ),
        receptionist_tone="warm and concise",
        refusal_behavior="Sorry sir, I can help only with {business_type}-related questions.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )
