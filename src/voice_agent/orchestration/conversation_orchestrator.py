from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from voice_agent.booking.extraction import looks_like_booking_request
from voice_agent.config import BusinessConfig
from voice_agent.conversational_booking import BookingFlowResult, ConversationalBookingFlow
from voice_agent.language import SessionLanguageSnapshot, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.human_takeover import (
    EscalationPriority,
    HumanTakeoverRuntime,
    TakeoverOwnershipState,
    TakeoverTransition,
)
from voice_agent.realtime_prompt_manager import PromptIntent
from voice_agent.retrieval import FAQRetrievalEngine, FAQRetrievalResult
from voice_agent.runtime_persistence import RuntimePersistenceSink
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


class ConversationRuntimeState(str, Enum):
    IDLE = "idle"
    GREETING = "greeting"
    SERVICE_DISCOVERY = "service_discovery"
    BOOKING_ACTIVE = "booking_active"
    BOOKING_CONFIRMATION = "booking_confirmation"
    FAQ_RESPONSE = "faq_response"
    ESCALATION_PENDING = "escalation_pending"
    HUMAN_HANDOVER = "human_handover"
    CALL_ENDING = "call_ending"
    INTERRUPTION_RECOVERY = "interruption_recovery"
    CLARIFICATION = "clarification"


class IntentRoute(str, Enum):
    BOOKING = "booking"
    FAQ = "faq"
    ESCALATION = "escalation"
    INTERRUPTION = "interruption"
    CORRECTION = "correction"
    GREETING = "greeting"
    UNCLEAR = "unclear"
    SERVICE_DISCOVERY = "service_discovery"
    CALL_ENDING = "call_ending"


@dataclass(frozen=True)
class RetryDecision:
    key: str
    count: int
    should_escalate: bool
    response_text: str | None


@dataclass(frozen=True)
class ConversationOrchestratorDecision:
    handled: bool
    route: IntentRoute
    current_state: ConversationRuntimeState
    previous_state: ConversationRuntimeState | None
    prompt_intent: PromptIntent
    response_text: str | None = None
    booking_result: BookingFlowResult | None = None
    faq_result: FAQRetrievalResult | None = None
    retry: RetryDecision | None = None
    escalation_reason: str | None = None
    human_takeover: TakeoverTransition | None = None
    workflow_stage: str = "idle"
    should_call_model: bool = True
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ConversationRuntimeSnapshot:
    current_state: ConversationRuntimeState
    previous_state: ConversationRuntimeState | None
    allowed_transitions: tuple[ConversationRuntimeState, ...]
    blocked_transitions: tuple[ConversationRuntimeState, ...]
    recovery_state: ConversationRuntimeState | None
    retry_counts: dict[str, int]
    interruption_count: int
    escalation_triggered: bool


@dataclass
class _IntentClassification:
    route: IntentRoute
    reason: str
    faq_result: FAQRetrievalResult | None = None
    matched_service: str | None = None


_ALLOWED_TRANSITIONS: dict[ConversationRuntimeState, frozenset[ConversationRuntimeState]] = {
    ConversationRuntimeState.IDLE: frozenset(
        {
            ConversationRuntimeState.GREETING,
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.GREETING: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.SERVICE_DISCOVERY: frozenset(
        {
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.BOOKING_ACTIVE: frozenset(
        {
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.BOOKING_CONFIRMATION,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.BOOKING_CONFIRMATION: frozenset(
        {
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.HUMAN_HANDOVER,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.FAQ_RESPONSE: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.ESCALATION_PENDING: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.BOOKING_CONFIRMATION,
            ConversationRuntimeState.HUMAN_HANDOVER,
            ConversationRuntimeState.CLARIFICATION,
            ConversationRuntimeState.CALL_ENDING,
        }
    ),
    ConversationRuntimeState.HUMAN_HANDOVER: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.BOOKING_CONFIRMATION,
            ConversationRuntimeState.CALL_ENDING,
        }
    ),
    ConversationRuntimeState.CALL_ENDING: frozenset(),
    ConversationRuntimeState.INTERRUPTION_RECOVERY: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.BOOKING_CONFIRMATION,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
    ConversationRuntimeState.CLARIFICATION: frozenset(
        {
            ConversationRuntimeState.SERVICE_DISCOVERY,
            ConversationRuntimeState.BOOKING_ACTIVE,
            ConversationRuntimeState.FAQ_RESPONSE,
            ConversationRuntimeState.ESCALATION_PENDING,
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            ConversationRuntimeState.CALL_ENDING,
            ConversationRuntimeState.CLARIFICATION,
        }
    ),
}

_ALL_STATES = frozenset(ConversationRuntimeState)
_BOOKING_RETRY_PREFIXES = (
    "invalid_phone_number:",
    "invalid_time:",
    "ambiguous_time:",
    "unclear_confirmation:",
    "booking_silence:",
    "noisy_transcript:",
)
_GREETING_RE = re.compile(r"\b(?:hello|hi|hey|namaste|good morning|good evening)\b", re.I)
_INTERRUPTION_RE = re.compile(
    r"\b(?:wait|hold on|one second|ek minute|ruk|ruko|sorry|actually|stop|let me|no wait)\b",
    re.I,
)
_CORRECTION_RE = re.compile(
    r"\b(?:actually|instead|change|make it|rather|sorry|not that|wrong|galat|badal|badlo)\b",
    re.I,
)
_ESCALATION_RE = re.compile(
    r"\b(?:human|person|receptionist|manager|supervisor|doctor now|talk to someone|"
    r"emergency|urgent|severe pain|bleeding|angry|frustrated|complaint|useless|"
    r"operator|agent)\b",
    re.I,
)
_SERVICE_DISCOVERY_RE = re.compile(
    r"\b(?:services?|treatments?|what do you|do you provide|available|offer)\b",
    re.I,
)
_UNCLEAR_RE = re.compile(r"\b(?:unclear|noise|inaudible|can't hear|cannot hear|repeat)\b", re.I)
_END_CALL_RE = re.compile(r"\b(?:bye|goodbye|that's all|thank you|thanks)\b", re.I)


class ConversationOrchestrator:
    """Deterministic runtime brain for live call workflow control.

    The orchestrator owns routing and state transitions. It delegates booking
    extraction to the existing workflow, FAQ lookup to deterministic retrieval,
    and leaves GPT only the language-generation fallback when runtime has no
    deterministic response to send.
    """

    def __init__(
        self,
        business_config: BusinessConfig,
        *,
        session_id: str,
        booking_flow: ConversationalBookingFlow | None = None,
        faq_retrieval_engine: FAQRetrievalEngine | None = None,
        persistence_sink: RuntimePersistenceSink | None = None,
        human_takeover_runtime: HumanTakeoverRuntime | None = None,
        initial_state: ConversationRuntimeState = ConversationRuntimeState.IDLE,
        max_retries_per_key: int = 3,
        max_interruption_storm: int = 4,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._business_config = business_config
        self._session_id = session_id
        self._booking_flow = booking_flow or ConversationalBookingFlow(
            business_config,
            persistence_sink=persistence_sink,
        )
        self._faq_retrieval_engine = faq_retrieval_engine or FAQRetrievalEngine(top_k=1)
        self._human_takeover_runtime = human_takeover_runtime or HumanTakeoverRuntime(clock=clock)
        self._max_retries_per_key = max_retries_per_key
        self._max_interruption_storm = max_interruption_storm
        self._clock = clock
        self._lock = threading.RLock()
        self._current_state = initial_state
        self._previous_state: ConversationRuntimeState | None = None
        self._recovery_state: ConversationRuntimeState | None = None
        self._retry_counts: dict[str, int] = {}
        self._interruption_count = 0
        self._turn_index = 0

    @property
    def current_state(self) -> ConversationRuntimeState:
        with self._lock:
            return self._current_state

    @property
    def previous_state(self) -> ConversationRuntimeState | None:
        with self._lock:
            return self._previous_state

    def snapshot(self) -> ConversationRuntimeSnapshot:
        with self._lock:
            allowed = _ALLOWED_TRANSITIONS[self._current_state]
            return ConversationRuntimeSnapshot(
                current_state=self._current_state,
                previous_state=self._previous_state,
                allowed_transitions=tuple(sorted(allowed, key=lambda state: state.value)),
                blocked_transitions=tuple(
                    sorted(_ALL_STATES - allowed - {self._current_state}, key=lambda state: state.value)
                ),
                recovery_state=self._recovery_state,
                retry_counts=dict(self._retry_counts),
                interruption_count=self._interruption_count,
                escalation_triggered=self._current_state
                in {
                    ConversationRuntimeState.ESCALATION_PENDING,
                    ConversationRuntimeState.HUMAN_HANDOVER,
                },
            )

    def transition(
        self,
        next_state: ConversationRuntimeState,
        *,
        reason: str,
        request_id: str | None = None,
        **fields: Any,
    ) -> bool:
        with self._lock:
            if next_state == self._current_state:
                return True
            if next_state not in _ALLOWED_TRANSITIONS[self._current_state]:
                log_event(
                    logger,
                    "state_transition",
                    request_id=request_id,
                    session_id=self._session_id,
                    conversation_state=self._current_state.value,
                    attempted_state=next_state.value,
                    allowed=False,
                    reason=reason,
                    **fields,
                )
                return False
            previous = self._current_state
            self._previous_state = previous
            self._current_state = next_state
            log_event(
                logger,
                "state_transition",
                request_id=request_id,
                session_id=self._session_id,
                previous_state=previous.value,
                conversation_state=next_state.value,
                current_state=next_state.value,
                allowed=True,
                reason=reason,
                **fields,
            )
            return True

    async def handle_turn(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
        interruption: bool = False,
    ) -> ConversationOrchestratorDecision:
        started_at = self._clock()
        cleaned = _clean(transcript)
        language = language or default_language_snapshot()
        self._turn_index += 1
        memory.update_language(language.active_language, request_id=request_id)
        memory.runtime_memory.update_language(language, request_id=request_id)

        classification = self._classify(
            cleaned,
            memory=memory,
            language=language,
            request_id=request_id,
            interruption=interruption,
        )
        log_event(
            logger,
            "intent_route",
            request_id=request_id,
            session_id=self._session_id,
            route=classification.route.value,
            reason=classification.reason,
            conversation_state=self.current_state.value,
            workflow_stage=memory.booking_stage.value,
            active_language=language.active_language,
        )

        try:
            if classification.route == IntentRoute.ESCALATION:
                return self._handle_escalation(
                    reason=classification.reason,
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )
            if classification.route == IntentRoute.INTERRUPTION:
                return self._handle_interruption(
                    cleaned,
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )
            if classification.route in {IntentRoute.BOOKING, IntentRoute.CORRECTION}:
                return await self._handle_booking(
                    cleaned,
                    route=classification.route,
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )
            if classification.route == IntentRoute.FAQ and classification.faq_result is not None:
                return self._handle_faq(
                    classification.faq_result,
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )
            if classification.route == IntentRoute.GREETING:
                return self._handle_greeting(
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )
            if classification.route == IntentRoute.UNCLEAR:
                return self._handle_unclear(
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                    reason=classification.reason,
                )
            if classification.route == IntentRoute.CALL_ENDING:
                return self._handle_call_ending(
                    memory=memory,
                    language=language,
                    request_id=request_id,
                    started_at=started_at,
                )

            self.transition(
                ConversationRuntimeState.SERVICE_DISCOVERY,
                reason=classification.reason,
                request_id=request_id,
                workflow_stage=memory.booking_stage.value,
            )
            return self._decision(
                handled=False,
                route=classification.route,
                response_text=None,
                prompt_intent=PromptIntent(
                    classification=classification.route.value,
                    matched_service=classification.matched_service,
                ),
                memory=memory,
                started_at=started_at,
            )
        except Exception as exc:
            log_event(
                logger,
                "conversation_orchestration_failed",
                request_id=request_id,
                session_id=self._session_id,
                error_type=type(exc).__name__,
                conversation_state=self.current_state.value,
                workflow_stage=memory.booking_stage.value,
            )
            return self._handle_escalation(
                reason="orchestration_failure",
                memory=memory,
                language=language,
                request_id=request_id,
                started_at=started_at,
            )

    def _classify(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        interruption: bool,
    ) -> _IntentClassification:
        if _END_CALL_RE.search(transcript) and memory.booking.confirmation_completed:
            return _IntentClassification(IntentRoute.CALL_ENDING, "call_wrap_up")
        if interruption or _is_interruption(transcript, memory):
            return _IntentClassification(IntentRoute.INTERRUPTION, "caller_interrupted_runtime")
        if _ESCALATION_RE.search(transcript) or memory.escalation_triggered:
            return _IntentClassification(IntentRoute.ESCALATION, "caller_escalation_request")
        if self._should_escalate_for_failures(memory):
            return _IntentClassification(IntentRoute.ESCALATION, "repeated_runtime_failures")
        if _is_correction(transcript, memory):
            return _IntentClassification(IntentRoute.CORRECTION, "booking_correction")
        faq_result = self._faq_retrieval_engine.retrieve(
            transcript=transcript,
            business=self._business_config,
            request_id=request_id,
        )
        is_booking_request = looks_like_booking_request(
            transcript,
            self._business_config.services,
        )
        if (
            faq_result.matches
            and faq_result.confidence >= (0.55 if _booking_active(memory) else 0.45)
            and not is_booking_request
        ):
            return _IntentClassification(
                IntentRoute.FAQ,
                "faq_retrieval_match",
                faq_result=faq_result,
            )
        if _booking_active(memory) or is_booking_request:
            return _IntentClassification(IntentRoute.BOOKING, "booking_workflow")

        if faq_result.matches and faq_result.confidence >= 0.45:
            return _IntentClassification(
                IntentRoute.FAQ,
                "faq_retrieval_match",
                faq_result=faq_result,
            )
        if _GREETING_RE.search(transcript):
            return _IntentClassification(IntentRoute.GREETING, "caller_greeting")
        if _UNCLEAR_RE.search(transcript) or len(transcript.split()) <= 1:
            return _IntentClassification(IntentRoute.UNCLEAR, "unclear_or_short_transcript")
        if _SERVICE_DISCOVERY_RE.search(transcript):
            matched_service = _match_service(transcript, self._business_config.services)
            return _IntentClassification(
                IntentRoute.SERVICE_DISCOVERY,
                "service_discovery",
                matched_service=matched_service,
            )
        if language.confidence < 0.35:
            return _IntentClassification(IntentRoute.UNCLEAR, "low_language_confidence")
        return _IntentClassification(IntentRoute.SERVICE_DISCOVERY, "business_context_fallback")

    async def _handle_booking(
        self,
        transcript: str,
        *,
        route: IntentRoute,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        self.transition(
            ConversationRuntimeState.BOOKING_ACTIVE,
            reason=route.value,
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        result = await self._booking_flow.handle_turn(
            transcript,
            memory=memory,
            language=language,
            request_id=request_id,
        )
        next_state = _state_for_booking_result(result, memory)
        self.transition(
            next_state,
            reason=f"booking_{result.status}",
            request_id=request_id,
            workflow_stage=result.booking_stage,
            pending_booking_fields=list(result.pending_fields),
        )

        retry = self._retry_from_booking_result(result, memory, request_id=request_id)
        if retry is not None and retry.should_escalate:
            return self._handle_escalation(
                reason=f"retry_exhausted:{retry.key}",
                memory=memory,
                language=language,
                request_id=request_id,
                started_at=started_at,
            )

        response_text = result.response_text if result.handled else None
        return self._decision(
            handled=result.handled and response_text is not None,
            route=route,
            response_text=response_text,
            prompt_intent=PromptIntent(
                classification=route.value,
                generation_source="runtime",
            ),
            booking_result=result,
            retry=retry,
            memory=memory,
            started_at=started_at,
            should_call_model=not (result.handled and response_text is not None),
        )

    def _handle_faq(
        self,
        faq_result: FAQRetrievalResult,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        self.transition(
            ConversationRuntimeState.FAQ_RESPONSE,
            reason="faq_retrieval_match",
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
            faq_confidence=faq_result.confidence,
            selected_faq_questions=list(faq_result.selected_questions),
        )
        memory.record_faq_retrieval(
            matched_count=len(faq_result.matches),
            confidence=faq_result.confidence,
            injected_chars=faq_result.injected_chars,
            latency_ms=faq_result.latency_ms,
            source=faq_result.source,
            selected_questions=faq_result.selected_questions,
            request_id=request_id,
            failure_reason=faq_result.failure_reason,
        )
        answer = _localized_faq_answer(faq_result.matches[0].faq.answer, language)
        return self._decision(
            handled=True,
            route=IntentRoute.FAQ,
            response_text=answer,
            prompt_intent=PromptIntent(
                classification="faq",
                generation_source="runtime",
                matched_faq=faq_result.matches[0].faq.question,
            ),
            faq_result=faq_result,
            memory=memory,
            started_at=started_at,
            should_call_model=False,
        )

    def _handle_interruption(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        with self._lock:
            self._interruption_count += 1
            self._recovery_state = self._current_state
        self.transition(
            ConversationRuntimeState.INTERRUPTION_RECOVERY,
            reason="caller_interruption",
            request_id=request_id,
            interruption_count=self._interruption_count,
            recovery_state=self._recovery_state.value if self._recovery_state else None,
        )
        if self._interruption_count >= self._max_interruption_storm:
            return self._handle_escalation(
                reason="interruption_storm",
                memory=memory,
                language=language,
                request_id=request_id,
                started_at=started_at,
            )

        recovery_state = _recoverable_state(self._recovery_state, memory)
        self.transition(
            recovery_state,
            reason="interruption_recovery",
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        log_event(
            logger,
            "interruption_recovery",
            request_id=request_id,
            session_id=self._session_id,
            conversation_state=self.current_state.value,
            recovery_state=recovery_state.value,
            workflow_stage=memory.booking_stage.value,
            interruption_count=self._interruption_count,
            preserved_booking_fields=list(memory.booking_values()),
        )
        return self._decision(
            handled=False,
            route=IntentRoute.INTERRUPTION,
            response_text=None,
            prompt_intent=PromptIntent(
                classification="interruption_recovery",
                generation_source="runtime",
            ),
            memory=memory,
            started_at=started_at,
        )

    def _handle_escalation(
        self,
        *,
        reason: str,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        memory.mark_escalation(reason=reason, request_id=request_id)
        self.transition(
            ConversationRuntimeState.ESCALATION_PENDING,
            reason=reason,
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        takeover = self._human_takeover_runtime.request_takeover(
            memory=memory,
            language=language,
            reason=reason,
            priority=_priority_for_escalation(reason),
            workflow_state=memory.booking_stage.value,
            request_id=request_id,
        )
        if takeover.ownership_state == TakeoverOwnershipState.AI_RESUMED:
            self.transition(
                _recoverable_state(self.previous_state, memory),
                reason="human_takeover_recovered_to_ai",
                request_id=request_id,
                workflow_stage=memory.booking_stage.value,
            )
        log_event(
            logger,
            "escalation_trigger",
            request_id=request_id,
            session_id=self._session_id,
            escalation_reason=reason,
            escalation_events=1,
            conversation_state=self.current_state.value,
            workflow_stage=memory.booking_stage.value,
            ownership_state=takeover.ownership_state.value,
            assigned_operator_id=takeover.assigned_operator_id,
        )
        return self._decision(
            handled=True,
            route=IntentRoute.ESCALATION,
            response_text=takeover.transition_message,
            prompt_intent=PromptIntent(
                classification="escalation",
                generation_source="runtime",
            ),
            escalation_reason=reason,
            human_takeover=takeover,
            memory=memory,
            started_at=started_at,
            should_call_model=False,
        )

    def _handle_greeting(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        self.transition(
            ConversationRuntimeState.GREETING,
            reason="caller_greeting",
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        return self._decision(
            handled=False,
            route=IntentRoute.GREETING,
            response_text=None,
            prompt_intent=PromptIntent(
                classification="greeting",
                generation_source="runtime",
            ),
            memory=memory,
            started_at=started_at,
        )

    def _handle_unclear(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
        reason: str,
    ) -> ConversationOrchestratorDecision:
        self.transition(
            ConversationRuntimeState.CLARIFICATION,
            reason=reason,
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        retry = self._increment_retry("unclear:transcript", request_id=request_id)
        if retry.should_escalate:
            return self._handle_escalation(
                reason="unclear_retry_exhausted",
                memory=memory,
                language=language,
                request_id=request_id,
                started_at=started_at,
            )
        return self._decision(
            handled=True,
            route=IntentRoute.UNCLEAR,
            response_text=retry.response_text or _localized_clarification(language, retry.count),
            prompt_intent=PromptIntent(
                classification="clarification",
                generation_source="runtime",
            ),
            retry=retry,
            memory=memory,
            started_at=started_at,
            should_call_model=False,
        )

    def _handle_call_ending(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        request_id: str | None,
        started_at: float,
    ) -> ConversationOrchestratorDecision:
        self.transition(
            ConversationRuntimeState.CALL_ENDING,
            reason="caller_ended_call",
            request_id=request_id,
            workflow_stage=memory.booking_stage.value,
        )
        return self._decision(
            handled=True,
            route=IntentRoute.CALL_ENDING,
            response_text=_localized_call_ending(language),
            prompt_intent=PromptIntent(
                classification="call_ending",
                generation_source="runtime",
            ),
            memory=memory,
            started_at=started_at,
            should_call_model=False,
        )

    def _retry_from_booking_result(
        self,
        result: BookingFlowResult,
        memory: CallSessionMemory,
        *,
        request_id: str | None,
    ) -> RetryDecision | None:
        for key, count in sorted(memory.booking.retry_counts.items()):
            if count <= self._retry_counts.get(key, 0):
                continue
            if not key.startswith(_BOOKING_RETRY_PREFIXES):
                continue
            self._retry_counts[key] = count
            should_escalate = count >= self._max_retries_per_key
            log_event(
                logger,
                "retry_count",
                request_id=request_id,
                session_id=self._session_id,
                retry_key=key,
                retry_count=count,
                should_escalate=should_escalate,
                workflow_stage=result.booking_stage,
                conversation_state=self.current_state.value,
            )
            return RetryDecision(
                key=key,
                count=count,
                should_escalate=should_escalate,
                response_text=result.response_text,
            )
        return None

    def _increment_retry(self, key: str, *, request_id: str | None) -> RetryDecision:
        count = self._retry_counts.get(key, 0) + 1
        self._retry_counts[key] = count
        should_escalate = count >= self._max_retries_per_key
        log_event(
            logger,
            "retry_count",
            request_id=request_id,
            session_id=self._session_id,
            retry_key=key,
            retry_count=count,
            should_escalate=should_escalate,
            conversation_state=self.current_state.value,
        )
        return RetryDecision(
            key=key,
            count=count,
            should_escalate=should_escalate,
            response_text=None,
        )

    def _should_escalate_for_failures(self, memory: CallSessionMemory) -> bool:
        if any(count >= self._max_retries_per_key for count in self._retry_counts.values()):
            return True
        return any(count >= self._max_retries_per_key for count in memory.booking.retry_counts.values())

    def _decision(
        self,
        *,
        handled: bool,
        route: IntentRoute,
        response_text: str | None,
        prompt_intent: PromptIntent,
        memory: CallSessionMemory,
        started_at: float,
        booking_result: BookingFlowResult | None = None,
        faq_result: FAQRetrievalResult | None = None,
        retry: RetryDecision | None = None,
        escalation_reason: str | None = None,
        human_takeover: TakeoverTransition | None = None,
        should_call_model: bool = True,
    ) -> ConversationOrchestratorDecision:
        latency_ms = round((self._clock() - started_at) * 1000, 3)
        decision = ConversationOrchestratorDecision(
            handled=handled,
            route=route,
            current_state=self.current_state,
            previous_state=self.previous_state,
            prompt_intent=prompt_intent,
            response_text=response_text,
            booking_result=booking_result,
            faq_result=faq_result,
            retry=retry,
            escalation_reason=escalation_reason,
            human_takeover=human_takeover,
            workflow_stage=memory.booking_stage.value,
            should_call_model=should_call_model,
            latency_ms=latency_ms,
        )
        log_event(
            logger,
            "conversation_state",
            session_id=self._session_id,
            conversation_state=decision.current_state.value,
            previous_state=decision.previous_state.value if decision.previous_state else None,
            intent_route=route.value,
            retry_count=retry.count if retry else 0,
            escalation_triggered=escalation_reason is not None or memory.escalation_triggered,
            ownership_state=(
                human_takeover.ownership_state.value
                if human_takeover is not None
                else None
            ),
            workflow_stage=decision.workflow_stage,
            should_call_model=decision.should_call_model,
            latency_ms=latency_ms,
            latency_target_ms=80,
            over_latency_target=latency_ms > 80,
            orchestration_latency=latency_ms,
            retrieval_latency=faq_result.latency_ms if faq_result is not None else None,
            memory_latency=None,
            gpt_latency=None,
            tts_latency=None,
            total_response_time=None,
            response_latency=None,
        )
        return decision


def _state_for_booking_result(
    result: BookingFlowResult,
    memory: CallSessionMemory,
) -> ConversationRuntimeState:
    if memory.booking.awaiting_confirmation or result.status == "awaiting_confirmation":
        return ConversationRuntimeState.BOOKING_CONFIRMATION
    if memory.booking.confirmation_completed or result.status == "complete":
        return ConversationRuntimeState.SERVICE_DISCOVERY
    return ConversationRuntimeState.BOOKING_ACTIVE


def _booking_active(memory: CallSessionMemory) -> bool:
    return bool(
        memory.booking_values()
        or memory.booking.awaiting_confirmation
        or memory.booking.confirmation_completed
        or memory.booking_stage.value != "idle"
    )


def _is_interruption(transcript: str, memory: CallSessionMemory) -> bool:
    if not transcript:
        return False
    if _CORRECTION_RE.search(transcript) and _booking_active(memory):
        return False
    return bool(_INTERRUPTION_RE.search(transcript) and _booking_active(memory))


def _is_correction(transcript: str, memory: CallSessionMemory) -> bool:
    return _booking_active(memory) and bool(_CORRECTION_RE.search(transcript))


def _recoverable_state(
    previous: ConversationRuntimeState | None,
    memory: CallSessionMemory,
) -> ConversationRuntimeState:
    if memory.booking.awaiting_confirmation:
        return ConversationRuntimeState.BOOKING_CONFIRMATION
    if _booking_active(memory):
        return ConversationRuntimeState.BOOKING_ACTIVE
    if previous in {
        ConversationRuntimeState.BOOKING_ACTIVE,
        ConversationRuntimeState.BOOKING_CONFIRMATION,
        ConversationRuntimeState.FAQ_RESPONSE,
        ConversationRuntimeState.SERVICE_DISCOVERY,
        ConversationRuntimeState.CLARIFICATION,
    }:
        return previous
    return ConversationRuntimeState.SERVICE_DISCOVERY


def _match_service(transcript: str, services: tuple[str, ...]) -> str | None:
    normalized = transcript.lower()
    for service in services:
        if service.lower() in normalized:
            return service
    return None


def _localized_escalation(language: SessionLanguageSnapshot) -> str:
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        return "I understand. Main clinic team ko handover kar raha hoon."
    return "I understand. I am handing this over to the clinic team."


def _localized_clarification(language: SessionLanguageSnapshot, attempt: int) -> str:
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        if attempt == 1:
            return "Sorry, mujhe clear nahi hua. Aap thoda repeat karenge?"
        return "Sorry, line clear nahi hai. Aap ek baar dheere se bata sakte hain?"
    if attempt == 1:
        return "Sorry, I did not catch that clearly. Could you repeat it?"
    return "Sorry, the line is not clear. Could you say that once more slowly?"


def _localized_call_ending(language: SessionLanguageSnapshot) -> str:
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        return "Thank you. Clinic team aapko zaroor follow up karegi."
    return "Thank you. The clinic team will follow up with you."


def _localized_faq_answer(answer: str, language: SessionLanguageSnapshot) -> str:
    cleaned = _clean(answer)
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        return cleaned
    return cleaned


def _priority_for_escalation(reason: str) -> EscalationPriority:
    if reason == "emergency":
        return EscalationPriority.EMERGENCY
    if reason in {"caller_escalation_request", "caller_frustration", "booking_failure_loop"}:
        return EscalationPriority.HIGH
    if reason.startswith("retry_exhausted") or reason in {
        "interruption_storm",
        "orchestration_failure",
        "unclear_retry_exhausted",
        "repeated_runtime_failures",
    }:
        return EscalationPriority.HIGH
    return EscalationPriority.NORMAL


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())
