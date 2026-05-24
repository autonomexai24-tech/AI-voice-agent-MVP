from __future__ import annotations

import asyncio

from voice_agent.config import BusinessConfig, OpenAIConfig
from voice_agent.language import SessionLanguageRouter
from voice_agent.providers.openai_text import OpenAIResponseClient, extract_output_text


class _FakeResponse:
    id = "resp_123"
    output_text = "Sure, I can help with that."


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse()


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def test_generate_response_uses_dynamic_language_prompt_and_no_conversation_state() -> None:
    asyncio.run(_run_generate_response_test())


async def _run_generate_response_test() -> None:
    config = OpenAIConfig(
        api_key="key",
        model="gpt-4o-mini",
        max_output_tokens=80,
        timeout_seconds=12.0,
        queue_max_items=50,
    )
    client = OpenAIResponseClient(config)
    fake_client = _FakeClient()
    client._client = fake_client
    language_router = SessionLanguageRouter(initial_language="english")
    language = (
        await language_router.route_text(
            "mujhe appointment chahiye",
            request_id="req-1",
            is_final=True,
        )
    ).snapshot

    response = await client.generate_response("Can I speak to reception?", language=language)

    assert response.text == "Sure, I can help with that."
    assert response.model == "gpt-4o-mini"
    assert response.language == "hinglish"
    assert "Realtime context" in fake_client.responses.kwargs["instructions"] or "Runtime memory:" in fake_client.responses.kwargs["instructions"]
    assert "Respond in natural Hinglish" in fake_client.responses.kwargs["instructions"]
    assert len(fake_client.responses.kwargs["instructions"]) <= 1500
    assert "Caller language: Hinglish" in fake_client.responses.kwargs["input"]
    assert fake_client.responses.kwargs["model"] == "gpt-4o-mini"
    assert fake_client.responses.kwargs["store"] is False
    assert "previous_response_id" not in fake_client.responses.kwargs
    assert "conversation" not in fake_client.responses.kwargs


def test_generate_response_does_not_own_booking_runtime() -> None:
    asyncio.run(_run_generate_response_booking_test())


async def _run_generate_response_booking_test() -> None:
    config = OpenAIConfig(
        api_key="key",
        model="gpt-4o-mini",
        max_output_tokens=80,
        timeout_seconds=12.0,
        queue_max_items=50,
    )
    client = OpenAIResponseClient(config, business_config=_business_config())
    fake_client = _FakeClient()
    client._client = fake_client

    response = await client.generate_response(
        "I want to book dental cleaning tomorrow at 10 AM",
        language=SessionLanguageRouter(initial_language="english").snapshot(),
    )

    assert response.text == "Yes sir, we provide dental cleaning at Smile Dental Clinic."
    assert response.response_id is None
    assert fake_client.responses.kwargs is None
    assert client._session_memory.booking.selected_service is None
    assert client._session_memory.booking.preferred_date is None
    assert client._session_memory.booking.preferred_time is None


def test_extract_output_text_accepts_output_text_property() -> None:
    assert extract_output_text(_FakeResponse()) == "Sure, I can help with that."


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment"),
        faqs=(),
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm",
        context_path=None,
    )
