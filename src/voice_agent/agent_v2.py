"""Official LiveKit + Sarvam plugin architecture agent.

Replaces the custom 4-queue pipeline in agent_phase1d.py with:
- sarvam.STT plugin (handles VAD, endpointing, flush signals)
- openai.LLM plugin (handles chat completions)
- sarvam.TTS plugin (handles speech synthesis)
- AgentSession with turn_detection="stt" (handles turn-taking, interruptions)

All realtime orchestration (transcript buffering, interruption state machine,
manual playback control, queue management) is now handled by the LiveKit
agents framework and the Sarvam plugin internally.

Business logic (prompts, guardrails, language behavior) is preserved
through the instructions parameter.
"""
from __future__ import annotations

from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import openai, sarvam

from voice_agent.conversation.orchestrator import ConversationOrchestrator
from voice_agent.conversation.states import ConversationState, PendingAction
from voice_agent.config import BusinessConfig
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.logging_config import get_logger, log_event
from voice_agent.prompts.composer import PromptContext, compose_prompt
from voice_agent.runtime_persistence import RuntimePersistenceSink, safe_enqueue
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


def build_instructions(business: BusinessConfig) -> str:
    """Build complete agent instructions from business configuration.

    Combines the base system prompt, business context (services, FAQs,
    tone, personality), guardrails, and language behavior into a single
    instructions string for the Agent.
    """
    return compose_prompt(PromptContext(business=business)).instructions


class SimpleClinicAgent(Agent):
    """Voice receptionist agent using official Sarvam + OpenAI plugins.

    Replaces custom STT websocket, TTS HTTP calls, interruption manager,
    transcript queues, and playback control with plugin-managed pipeline
    where Sarvam handles VAD, endpointing, and flush signals internally.
    """

    def __init__(
        self,
        *,
        instructions: str,
        stt_language: str = "unknown",
        stt_model: str = "saaras:v3",
        llm_model: str = "gpt-4o-mini",
        tts_language_code: str = "en-IN",
        tts_model: str = "bulbul:v3",
        tts_speaker: str = "kavya",
        business_config: BusinessConfig | None = None,
        session_id: str = "livekit-agent-session",
        persistence_sink: RuntimePersistenceSink | None = None,
    ) -> None:
        self.session_memory = CallSessionMemory(session_id=session_id)
        self._persistence_sink = persistence_sink
        self.conversation = ConversationOrchestrator(
            initial_state=ConversationState.IDLE,
            pending_action=PendingAction.NONE,
            session_id=session_id,
        )
        self._booking_flow = (
            ConversationalBookingFlow(
                business_config,
                persistence_sink=persistence_sink,
            )
            if business_config is not None
            else None
        )
        super().__init__(
            instructions=instructions,
            stt=sarvam.STT(
                language=stt_language,
                model=stt_model,
                mode="transcribe",
                flush_signal=True,
            ),
            llm=openai.LLM(model=llm_model),
            tts=sarvam.TTS(
                target_language_code=tts_language_code,
                model=tts_model,
                speaker=tts_speaker,
            ),
        )
        log_event(
            logger,
            "agent_created",
            stt_language=stt_language,
            stt_model=stt_model,
            llm_model=llm_model,
            tts_language_code=tts_language_code,
            tts_model=tts_model,
            tts_speaker=tts_speaker,
            instructions_chars=len(instructions),
        )

    async def on_enter(self):
        """Called when the caller joins — agent initiates the greeting."""
        log_event(logger, "agent_session_entered")
        self.conversation.transition(
            ConversationState.GREETING,
            reason="agent_entered_room",
            pending_action=PendingAction.PLAY_RESPONSE,
        )
        self.conversation.mark_ai_speaking_started(
            request_id=None,
            response_id=None,
            reason="initial_greeting",
        )
        self.session.generate_reply()

    async def on_user_turn_completed(self, *args, **kwargs):
        """Best-effort memory capture for the official AgentSession path."""
        text = _extract_turn_text(*args, **kwargs)
        if not text:
            return
        self.conversation.mark_ai_speaking_stopped(
            request_id=None,
            response_id=None,
            reason="user_turn_completed",
        )
        self.conversation.transition(
            ConversationState.LISTENING,
            reason="user_turn_started",
            pending_action=PendingAction.WAIT_FOR_CALLER,
        )
        self.conversation.mark_caller_speaking(
            request_id=None,
            source="agent_session_turn",
        )
        self.conversation.mark_caller_stopped(
            request_id=None,
            source="agent_session_turn",
        )
        self.conversation.transition(
            ConversationState.THINKING,
            reason="user_turn_completed",
            pending_action=PendingAction.GENERATE_RESPONSE,
        )
        self.session_memory.record_turn(role="caller", text=text)
        safe_enqueue(
            self._persistence_sink,
            "enqueue_transcript",
            call_id=self.session_memory.session_id,
            speaker="caller",
            text=text,
            language=self.session_memory.language,
        )
        if self._booking_flow is not None:
            result = await self._booking_flow.handle_turn(
                text,
                memory=self.session_memory,
            )
            self.conversation.set_booking_stage_from_pending(
                result.pending_fields,
                reason="booking_turn_completed",
            )


def _extract_turn_text(*args, **kwargs) -> str | None:
    candidates = list(args) + list(kwargs.values())
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        content = getattr(candidate, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text_parts = [part for part in content if isinstance(part, str)]
            if text_parts:
                return " ".join(text_parts).strip()
        text = getattr(candidate, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None
