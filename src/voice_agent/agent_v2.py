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

from voice_agent.conversation.orchestrator import ConversationOrchestrator as TurnStateOrchestrator
from voice_agent.conversation.states import ConversationState, PendingAction
from voice_agent.config import BusinessConfig
from voice_agent.human_takeover import HumanTakeoverRuntime
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.optimization import RuntimeLatencyOptimizer
from voice_agent.orchestration import ConversationOrchestrator as RuntimeConversationOrchestrator
from voice_agent.realtime_prompt_manager import PromptIntent, RealtimePromptManager
from voice_agent.retrieval import FAQRetrievalEngine
from voice_agent.runtime_persistence import RuntimePersistenceSink, safe_enqueue
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


def build_instructions(business: BusinessConfig) -> str:
    """Build compact bootstrap instructions from business configuration.

    Per-turn dynamic instructions are recomposed by RealtimePromptManager
    when caller text is available.
    """
    memory = CallSessionMemory(session_id="livekit-bootstrap")
    return RealtimePromptManager().compose(
        transcript="",
        business=business,
        memory=memory,
        language=default_language_snapshot(),
        intent=PromptIntent(classification="session_bootstrap"),
    ).instructions


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
        self._business_config = business_config
        self._optimizer = RuntimeLatencyOptimizer()
        self._prompt_manager = RealtimePromptManager(
            prompt_cache=self._optimizer.prompt_cache,
            retrieval_cache=self._optimizer.retrieval_cache,
            latency_profiler=self._optimizer.profiler,
        )
        self._language_router = SessionLanguageRouter(
            initial_language="english",
            default_speaker=tts_speaker,
        )
        self.human_takeover_runtime = HumanTakeoverRuntime()
        self.conversation = TurnStateOrchestrator(
            initial_state=ConversationState.IDLE,
            pending_action=PendingAction.NONE,
            session_id=session_id,
        )
        self._runtime_orchestrator = (
            RuntimeConversationOrchestrator(
                business_config,
                session_id=session_id,
                faq_retrieval_engine=FAQRetrievalEngine(
                    top_k=1,
                    retrieval_cache=self._optimizer.retrieval_cache,
                ),
                persistence_sink=persistence_sink,
                human_takeover_runtime=self.human_takeover_runtime,
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
        async with self._optimizer.profiler.async_span("stt", request_id=None):
            language = (
                await self._language_router.route_text(
                    text,
                    request_id=None,
                    is_final=True,
                )
            ).snapshot
        with self._optimizer.profiler.span("memory_assembly", request_id=None):
            self.session_memory.update_language(language.active_language)
            self.session_memory.runtime_memory.update_language(language)
        safe_enqueue(
            self._persistence_sink,
            "enqueue_transcript",
            call_id=self.session_memory.session_id,
            speaker="caller",
            text=text,
            language=language.active_language,
        )
        orchestration_decision = None
        if self._runtime_orchestrator is not None:
            async with self._optimizer.profiler.async_span("orchestration", request_id=None):
                orchestration_decision = await self._runtime_orchestrator.handle_turn(
                    text,
                    memory=self.session_memory,
                    language=language,
                    request_id=None,
                )
            if orchestration_decision.booking_result is not None:
                self.conversation.set_booking_stage_from_pending(
                    orchestration_decision.booking_result.pending_fields,
                    reason="booking_turn_completed",
                )
            if orchestration_decision.response_text is not None:
                self.session_memory.record_turn(
                    role="assistant",
                    text=orchestration_decision.response_text,
                )
        if self._business_config is not None:
            prompt_intent = (
                orchestration_decision.prompt_intent
                if orchestration_decision is not None
                else PromptIntent(classification="livekit_user_turn")
            )
            runtime_prompt = self._prompt_manager.compose(
                transcript=text,
                business=self._business_config,
                memory=self.session_memory,
                language=language,
                intent=prompt_intent,
            )
            instructions = runtime_prompt.instructions
            if orchestration_decision is not None and orchestration_decision.response_text:
                instructions = (
                    f"{instructions}\n"
                    "Runtime workflow decision: respond with this operational result, "
                    "without adding new workflow steps: "
                    f"{orchestration_decision.response_text}"
                )
            await self.update_instructions(instructions)
            log_event(
                logger,
                "agent_instructions_recomposed",
                prompt_chars=runtime_prompt.prompt_chars,
                prompt_size=runtime_prompt.prompt_chars,
                compression_ratio=runtime_prompt.compression_ratio,
                memory_pruned=runtime_prompt.memory_pruned,
                cache_hits=runtime_prompt.cache_hits,
                cache_misses=runtime_prompt.cache_misses,
                memory_injection_chars=runtime_prompt.memory_chars,
                faq_injection_count=runtime_prompt.faq_injection_count,
                faq_injection_chars=runtime_prompt.faq_injection_chars,
                faq_retrieval_confidence=runtime_prompt.faq_retrieval_confidence,
                faq_retrieval_latency_ms=runtime_prompt.faq_retrieval_latency_ms,
                retrieval_latency=runtime_prompt.faq_retrieval_latency_ms,
                faq_retrieval_source=runtime_prompt.faq_retrieval_source,
                selected_faq_questions=list(runtime_prompt.selected_faq_questions),
                prompt_recomposition_index=runtime_prompt.recomposition_index,
                intent_route=(
                    orchestration_decision.route.value
                    if orchestration_decision is not None
                    else None
                ),
                conversation_state=(
                    orchestration_decision.current_state.value
                    if orchestration_decision is not None
                    else None
                ),
                ownership_state=(
                    orchestration_decision.human_takeover.ownership_state.value
                    if orchestration_decision is not None
                    and orchestration_decision.human_takeover is not None
                    else None
                ),
            )
            self._optimizer.log_response_summary(
                request_id=None,
                prompt_size=runtime_prompt.prompt_chars,
                compression_ratio=runtime_prompt.compression_ratio,
                memory_pruned=runtime_prompt.memory_pruned,
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
