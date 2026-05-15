from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from openai import AsyncOpenAI

from voice_agent.business_prompt import BusinessPromptOrchestrator, SYSTEM_PROMPT
from voice_agent.config import BusinessConfig, CalComConfig, Fast2SMSConfig, OpenAIConfig
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.language import (
    SessionLanguageSnapshot,
    default_language_snapshot,
)
from voice_agent.logging_config import get_logger, log_event
from voice_agent.prompts.composer import PromptContext, compose_prompt
from voice_agent.runtime_persistence import RuntimePersistenceSink, safe_enqueue
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


@dataclass(frozen=True)
class AIResponse:
    text: str
    model: str
    response_id: str | None
    language: str = "english"


class OpenAIResponseClient:
    def __init__(
        self,
        config: OpenAIConfig,
        *,
        business_config: BusinessConfig | None = None,
        calcom_config: CalComConfig | None = None,
        fast2sms_config: Fast2SMSConfig | None = None,
        notification_orchestrator: object | None = None,
        session_id: str | None = None,
        session_memory: CallSessionMemory | None = None,
        persistence_sink: RuntimePersistenceSink | None = None,
    ) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key, timeout=config.timeout_seconds)
        self._business_config = business_config or _default_business_config()
        self._business_prompt = BusinessPromptOrchestrator(
            self._business_config
        )
        self._session_memory = session_memory or CallSessionMemory(
            session_id=session_id or "openai-response-client"
        )
        self._persistence_sink = persistence_sink
        self._booking_flow = ConversationalBookingFlow(
            self._business_config,
            persistence_sink=persistence_sink,
        )
        self._calcom_config = calcom_config
        self._fast2sms_config = fast2sms_config
        self._notification_orchestrator = notification_orchestrator

    async def aclose(self) -> None:
        await self._client.close()

    async def load_business_prompt(self) -> None:
        await self._business_prompt.load()

    async def generate_response(
        self,
        transcript: str,
        *,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
    ) -> AIResponse:
        started_at = time.perf_counter()
        cleaned_transcript = transcript.strip()
        if not cleaned_transcript:
            raise ValueError("transcript must not be empty")

        language = language or default_language_snapshot()
        self._session_memory.update_language(
            language.active_language,
            request_id=request_id,
        )
        self._session_memory.record_turn(
            role="caller",
            text=cleaned_transcript,
            request_id=request_id,
        )
        safe_enqueue(
            self._persistence_sink,
            "enqueue_transcript",
            call_id=self._session_memory.session_id,
            speaker="caller",
            text=cleaned_transcript,
            language=language.active_language,
            request_id=request_id,
        )

        booking_result = await self._booking_flow.handle_turn(
            cleaned_transcript,
            memory=self._session_memory,
            language=language,
            request_id=request_id,
        )
        if booking_result.handled and booking_result.response_text is not None:
            self._session_memory.record_turn(
                role="assistant",
                text=booking_result.response_text,
                request_id=request_id,
            )
            safe_enqueue(
                self._persistence_sink,
                "enqueue_transcript",
                call_id=self._session_memory.session_id,
                speaker="assistant",
                text=booking_result.response_text,
                language=language.active_language,
                request_id=request_id,
            )
            return AIResponse(
                text=booking_result.response_text,
                model=self._config.model,
                response_id=None,
                language=language.active_language,
            )

        decision = await self._business_prompt.prepare_response(
            cleaned_transcript,
            language=language,
            request_id=request_id,
        )
        log_event(
            logger,
            "business_response_generation_started",
            request_id=request_id,
            classification=decision.classification,
            generation_source=decision.generation_source,
            model=self._config.model,
            transcript_chars=len(cleaned_transcript),
            response_language=language.active_language,
            dominant_language=language.dominant_language,
            matched_service=decision.matched_service,
            requested_service=decision.requested_service,
        )

        if decision.response_text is not None:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
            log_event(
                logger,
                "business_response_generation_completed",
                request_id=request_id,
                classification=decision.classification,
                generation_source=decision.generation_source,
                model=self._config.model,
                response_chars=len(decision.response_text),
                latency_ms=latency_ms,
                response_language=language.active_language,
            )
            self._session_memory.record_turn(
                role="assistant",
                text=decision.response_text,
                request_id=request_id,
            )
            safe_enqueue(
                self._persistence_sink,
                "enqueue_transcript",
                call_id=self._session_memory.session_id,
                speaker="assistant",
                text=decision.response_text,
                language=language.active_language,
                request_id=request_id,
            )
            return AIResponse(
                text=decision.response_text,
                model=self._config.model,
                response_id=None,
                language=language.active_language,
            )

        if decision.instructions is None or decision.input_text is None:
            raise RuntimeError("business prompt decision did not include model inputs")

        business_context = await self._business_prompt.load()
        model_instructions = compose_prompt(
            PromptContext(
                business=business_context,
                language=language,
                booking_memory=self._session_memory,
            )
        ).instructions

        log_event(
            logger,
            "openai_response_request_started",
            request_id=request_id,
            model=self._config.model,
            transcript_chars=len(cleaned_transcript),
            max_output_tokens=self._config.max_output_tokens,
            response_language=language.active_language,
            dominant_language=language.dominant_language,
            language_confidence=language.confidence,
            language_generation=language.generation,
        )
        try:
            response = await self._client.responses.create(
                model=self._config.model,
                instructions=model_instructions,
                input=decision.input_text,
                max_output_tokens=self._config.max_output_tokens,
                temperature=0.3,
                store=False,
            )
        except Exception:
            log_event(
                logger,
                "business_response_generation_failed",
                request_id=request_id,
                classification=decision.classification,
                generation_source=decision.generation_source,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            raise

        text = extract_output_text(response).strip()
        if not text:
            raise RuntimeError("OpenAI response did not include output text")

        latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        response_id = getattr(response, "id", None)
        log_event(
            logger,
            "openai_response_request_completed",
            request_id=request_id,
            model=self._config.model,
            response_id=response_id,
            response_chars=len(text),
            response_language=language.active_language,
            dominant_language=language.dominant_language,
            language_generation=language.generation,
        )
        log_event(
            logger,
            "business_response_generation_completed",
            request_id=request_id,
            classification=decision.classification,
            generation_source=decision.generation_source,
            model=self._config.model,
            response_id=response_id,
            response_chars=len(text),
            latency_ms=latency_ms,
            response_language=language.active_language,
        )
        self._session_memory.record_turn(
            role="assistant",
            text=text,
            request_id=request_id,
        )
        safe_enqueue(
            self._persistence_sink,
            "enqueue_transcript",
            call_id=self._session_memory.session_id,
            speaker="assistant",
            text=text,
            language=language.active_language,
            request_id=request_id,
        )
        return AIResponse(
            text=text,
            model=self._config.model,
            response_id=response_id,
            language=language.active_language,
        )


def extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    chunks: list[str] = []
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""

    for item in output:
        content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def _default_business_config() -> BusinessConfig:
    return BusinessConfig(
        name="our clinic",
        business_type="clinic",
        services=(),
        faqs=(),
        receptionist_tone="warm, concise, respectful, and phone-friendly",
        refusal_behavior="Sorry sir, I can help only with {business_type}-related questions.",
        receptionist_personality="calm, attentive, practical, and helpful",
        context_path=None,
    )


def _default_calcom_config() -> CalComConfig:
    return CalComConfig(
        api_key=None,
        base_url="https://api.cal.com/v2",
        slots_api_version="2024-09-04",
        bookings_api_version="2026-02-25",
        event_type_id=None,
        event_type_slug=None,
        username=None,
        team_slug=None,
        organization_slug=None,
        time_zone="Asia/Kolkata",
        duration_minutes=30,
        timeout_seconds=8.0,
        retry_attempts=1,
        default_attendee_email=None,
    )


def _default_fast2sms_config() -> Fast2SMSConfig:
    return Fast2SMSConfig(
        api_key=None,
        base_url="https://www.fast2sms.com/dev/bulkV2",
        route="q",
        language="english",
        timeout_seconds=5.0,
        retry_attempts=1,
        queue_max_items=100,
        drain_timeout_seconds=3.0,
    )
