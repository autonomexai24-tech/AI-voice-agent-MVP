from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable

from voice_agent.booking.entities import BookingField
from voice_agent.conversation.orchestrator import booking_stage_from_pending_fields
from voice_agent.language import LANGUAGE_LABELS, SessionLanguageSnapshot, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)


class TakeoverOwnershipState(str, Enum):
    ACTIVE_AI = "ACTIVE_AI"
    PENDING_ESCALATION = "PENDING_ESCALATION"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    AI_RESUMED = "AI_RESUMED"
    CALL_COMPLETED = "CALL_COMPLETED"


class EscalationPriority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30
    EMERGENCY = 40


class EscalationQueueStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    TIMED_OUT = "timed_out"
    OVERFLOWED = "overflowed"
    RECOVERED = "recovered"
    COMPLETED = "completed"


@dataclass(frozen=True)
class EscalationPolicyDecision:
    should_escalate: bool
    reason: str | None
    priority: EscalationPriority
    trigger_source: str


@dataclass(frozen=True)
class HumanOperator:
    operator_id: str
    display_name: str
    languages: tuple[str, ...] = ("english", "hindi", "hinglish", "mixed")
    max_active_calls: int = 1


@dataclass(frozen=True)
class HumanOperatorAssignment:
    operator_id: str
    assigned_at: float
    queue_wait_ms: float
    language_match: bool


@dataclass(frozen=True)
class HumanHandoffPayload:
    session_id: str
    caller_identity: dict[str, str | None]
    booking_progress: dict[str, Any]
    unresolved_issues: tuple[dict[str, Any], ...]
    active_language: str
    language_note: str
    escalation_reason: str
    workflow_state: str
    memory_summary: str
    correction_history: dict[str, Any]
    generated_turn: int


@dataclass
class EscalationQueueItem:
    session_id: str
    priority: EscalationPriority
    payload: HumanHandoffPayload
    created_at: float
    status: EscalationQueueStatus = EscalationQueueStatus.PENDING
    assignment: HumanOperatorAssignment | None = None
    timeout_at: float | None = None


@dataclass(frozen=True)
class TakeoverTransition:
    session_id: str
    ownership_state: TakeoverOwnershipState
    transition_message: str
    handoff_payload: HumanHandoffPayload | None
    assigned_operator_id: str | None
    escalation_reason: str | None
    queue_wait_ms: float | None = None
    recovery_reason: str | None = None


@dataclass(frozen=True)
class TakeoverRuntimeSnapshot:
    session_id: str
    ownership_state: TakeoverOwnershipState
    escalation_reason: str | None
    assigned_operator_id: str | None
    queued: bool
    queue_position: int | None
    pending_count: int
    active_human_count: int
    last_recovery_reason: str | None


@dataclass
class _TakeoverSessionState:
    session_id: str
    ownership_state: TakeoverOwnershipState = TakeoverOwnershipState.ACTIVE_AI
    escalation_reason: str | None = None
    assigned_operator_id: str | None = None
    handoff_payload: HumanHandoffPayload | None = None
    state_started_at: float = 0.0
    takeover_started_at: float | None = None
    last_recovery_reason: str | None = None


_EMERGENCY_RE = re.compile(
    r"\b(?:emergency|urgent|severe pain|bleeding|fainted|unconscious|accident|"
    r"can't breathe|cannot breathe|chest pain)\b",
    re.I,
)
_ESCALATION_REQUEST_RE = re.compile(
    r"\b(?:human|person|operator|agent|receptionist|manager|supervisor|"
    r"talk to someone|speak to someone|doctor now)\b",
    re.I,
)
_FRUSTRATION_RE = re.compile(
    r"\b(?:angry|frustrated|complaint|useless|not helping|fed up|irritated|"
    r"annoyed|bad service|stop repeating)\b",
    re.I,
)
_UNSUPPORTED_RE = re.compile(
    r"\b(?:unsupported|not available|do not provide|outside scope|refund|insurance claim)\b",
    re.I,
)
_SIMPLE_CORRECTION_RE = re.compile(
    r"\b(?:actually|instead|change|make it|rather|sorry|not that|wrong)\b",
    re.I,
)

_FIELD_TO_ATTR = {
    "customer_name": "caller_name",
    "phone_number": "phone_number",
    "service_type": "selected_service",
    "appointment_date": "preferred_date",
    "appointment_time": "preferred_time",
    "doctor_preference": "doctor_preference",
    "notes": "optional_notes",
    "optional_notes": "optional_notes",
}


class EscalationQueue:
    """Small deterministic queue for AI calls waiting on human operators."""

    def __init__(
        self,
        *,
        operators: tuple[HumanOperator, ...] | None = None,
        max_active_escalations: int = 5,
        timeout_seconds: float = 45.0,
        aging_interval_seconds: float = 15.0,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if max_active_escalations < 1:
            raise ValueError("max_active_escalations must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._operators = (
            operators
            if operators is not None
            else (
                HumanOperator("supervisor-1", "Clinic Coordinator 1"),
                HumanOperator("supervisor-2", "Clinic Coordinator 2"),
            )
        )
        self._max_active_escalations = max_active_escalations
        self._timeout_seconds = timeout_seconds
        self._aging_interval_seconds = max(1.0, aging_interval_seconds)
        self._clock = clock
        self._items: dict[str, EscalationQueueItem] = {}
        self._operator_sessions: dict[str, str] = {}

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self._items.values() if item.status == EscalationQueueStatus.PENDING)

    @property
    def active_human_count(self) -> int:
        return len(self._operator_sessions)

    def enqueue(
        self,
        payload: HumanHandoffPayload,
        *,
        priority: EscalationPriority,
        request_id: str | None = None,
    ) -> EscalationQueueItem:
        existing = self._items.get(payload.session_id)
        if existing and existing.status in {
            EscalationQueueStatus.PENDING,
            EscalationQueueStatus.ASSIGNED,
        }:
            return existing

        active_items = [
            item
            for item in self._items.values()
            if item.status in {EscalationQueueStatus.PENDING, EscalationQueueStatus.ASSIGNED}
        ]
        if len(active_items) >= self._max_active_escalations:
            item = EscalationQueueItem(
                session_id=payload.session_id,
                priority=priority,
                payload=payload,
                created_at=self._clock(),
                status=EscalationQueueStatus.OVERFLOWED,
            )
            log_event(
                logger,
                "queue_overflow",
                request_id=request_id,
                session_id=payload.session_id,
                pending_count=self.pending_count,
                active_human_count=self.active_human_count,
                max_active_escalations=self._max_active_escalations,
            )
            return item

        now = self._clock()
        item = EscalationQueueItem(
            session_id=payload.session_id,
            priority=priority,
            payload=payload,
            created_at=now,
            timeout_at=now + self._timeout_seconds,
        )
        self._items[payload.session_id] = item
        log_event(
            logger,
            "escalation_queued",
            request_id=request_id,
            session_id=payload.session_id,
            queue_priority=priority.name.lower(),
            pending_count=self.pending_count,
            active_human_count=self.active_human_count,
        )
        return item

    def assign_next(self, *, request_id: str | None = None) -> EscalationQueueItem | None:
        pending = [
            item
            for item in self._items.values()
            if item.status == EscalationQueueStatus.PENDING
        ]
        if not pending:
            return None

        pending.sort(key=self._queue_sort_key)
        for item in pending:
            operator = self._available_operator(item.payload)
            if operator is None:
                return None
            now = self._clock()
            wait_ms = round((now - item.created_at) * 1000, 3)
            assignment = HumanOperatorAssignment(
                operator_id=operator.operator_id,
                assigned_at=now,
                queue_wait_ms=wait_ms,
                language_match=_operator_supports_language(operator, item.payload.active_language),
            )
            item.assignment = assignment
            item.status = EscalationQueueStatus.ASSIGNED
            self._operator_sessions[operator.operator_id] = item.session_id
            log_event(
                logger,
                "human_operator_assigned",
                request_id=request_id,
                session_id=item.session_id,
                operator_id=operator.operator_id,
                active_language=item.payload.active_language,
                language_match=assignment.language_match,
            )
            log_event(
                logger,
                "queue_wait_time",
                request_id=request_id,
                session_id=item.session_id,
                queue_wait_time=wait_ms,
                queue_wait_ms=wait_ms,
            )
            return item
        return None

    def release_assignment(
        self,
        session_id: str,
        *,
        completed: bool,
        request_id: str | None = None,
    ) -> None:
        item = self._items.get(session_id)
        if item is None:
            return
        if item.assignment is not None:
            self._operator_sessions.pop(item.assignment.operator_id, None)
        item.status = (
            EscalationQueueStatus.COMPLETED if completed else EscalationQueueStatus.RECOVERED
        )
        log_event(
            logger,
            "escalation_queue_released",
            request_id=request_id,
            session_id=session_id,
            completed=completed,
            pending_count=self.pending_count,
            active_human_count=self.active_human_count,
        )

    def recover_timeouts(self, *, request_id: str | None = None) -> tuple[EscalationQueueItem, ...]:
        now = self._clock()
        timed_out: list[EscalationQueueItem] = []
        for item in self._items.values():
            if item.status != EscalationQueueStatus.PENDING:
                continue
            if item.timeout_at is None or now < item.timeout_at:
                continue
            item.status = EscalationQueueStatus.TIMED_OUT
            timed_out.append(item)
            log_event(
                logger,
                "escalation_timeout",
                request_id=request_id,
                session_id=item.session_id,
                queue_wait_time=round((now - item.created_at) * 1000, 3),
                queue_wait_ms=round((now - item.created_at) * 1000, 3),
            )
        return tuple(timed_out)

    def queue_position(self, session_id: str) -> int | None:
        pending = [
            item
            for item in self._items.values()
            if item.status == EscalationQueueStatus.PENDING
        ]
        pending.sort(key=self._queue_sort_key)
        for index, item in enumerate(pending, start=1):
            if item.session_id == session_id:
                return index
        return None

    def get(self, session_id: str) -> EscalationQueueItem | None:
        return self._items.get(session_id)

    def _queue_sort_key(self, item: EscalationQueueItem) -> tuple[int, float, str]:
        waited = max(0.0, self._clock() - item.created_at)
        aging_boost = int(waited // self._aging_interval_seconds)
        effective_priority = int(item.priority) + aging_boost
        return (-effective_priority, item.created_at, item.session_id)

    def _available_operator(self, payload: HumanHandoffPayload) -> HumanOperator | None:
        available = [
            operator
            for operator in self._operators
            if operator.operator_id not in self._operator_sessions
        ]
        if not available:
            return None
        preferred = [
            operator
            for operator in available
            if _operator_supports_language(operator, payload.active_language)
        ]
        return (preferred or available)[0]


class HumanTakeoverRuntime:
    """Runtime-owned AI/human continuity layer.

    The runtime transfers compact call state to a human operator and can return
    the same conversation to AI without resetting booking or language memory.
    """

    def __init__(
        self,
        *,
        queue: EscalationQueue | None = None,
        handoff_max_chars: int = 900,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._queue = queue or EscalationQueue(clock=clock)
        self._handoff_max_chars = handoff_max_chars
        self._clock = clock
        self._states: dict[str, _TakeoverSessionState] = {}

    @property
    def queue(self) -> EscalationQueue:
        return self._queue

    def evaluate_escalation(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        faq_answer_exists: bool = False,
        unsupported_request: bool = False,
        interruption_count: int = 0,
        request_id: str | None = None,
    ) -> EscalationPolicyDecision:
        language = language or default_language_snapshot()
        cleaned = _clean(transcript)
        lowered = cleaned.lower()

        decision = self._policy_decision(
            cleaned,
            memory=memory,
            language=language,
            faq_answer_exists=faq_answer_exists,
            unsupported_request=unsupported_request,
            interruption_count=interruption_count,
        )
        log_event(
            logger,
            "escalation_trigger",
            request_id=request_id,
            session_id=memory.session_id,
            should_escalate=decision.should_escalate,
            escalation_events=1 if decision.should_escalate else 0,
            escalation_reason=decision.reason,
            trigger_source=decision.trigger_source,
            active_language=language.active_language,
            faq_answer_exists=faq_answer_exists,
            unsupported_request=unsupported_request,
            transcript_signal=_safe_signal(lowered),
        )
        return decision

    def request_takeover(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        reason: str,
        priority: EscalationPriority = EscalationPriority.NORMAL,
        workflow_state: str = "unknown",
        request_id: str | None = None,
    ) -> TakeoverTransition:
        started_at = self._clock()
        language = language or default_language_snapshot()
        state = self._state_for(memory.session_id)
        memory.mark_escalation(reason=reason, request_id=request_id)
        state.ownership_state = TakeoverOwnershipState.PENDING_ESCALATION
        state.escalation_reason = reason
        state.state_started_at = started_at
        log_event(
            logger,
            "escalation_reason",
            request_id=request_id,
            session_id=memory.session_id,
            escalation_reason=reason,
            escalation_events=1,
            priority=priority.name.lower(),
            workflow_state=workflow_state,
        )

        handoff = self.build_handoff_payload(
            memory=memory,
            language=language,
            reason=reason,
            workflow_state=workflow_state,
            request_id=request_id,
        )
        state.handoff_payload = handoff
        item = self._queue.enqueue(handoff, priority=priority, request_id=request_id)
        if item.status == EscalationQueueStatus.OVERFLOWED:
            return self._recover_to_ai(
                memory=memory,
                language=language,
                reason="queue_overflow",
                request_id=request_id,
                handoff=handoff,
            )

        assigned_item = self._queue.assign_next(request_id=request_id)
        active_item = self._queue.get(memory.session_id) or item
        if assigned_item is not None and assigned_item.session_id == memory.session_id:
            active_item = assigned_item

        if active_item.status == EscalationQueueStatus.ASSIGNED and active_item.assignment:
            state.ownership_state = TakeoverOwnershipState.HUMAN_ACTIVE
            state.assigned_operator_id = active_item.assignment.operator_id
            state.takeover_started_at = active_item.assignment.assigned_at
        else:
            state.ownership_state = TakeoverOwnershipState.WAITING_FOR_HUMAN
            state.assigned_operator_id = None

        latency_ms = round((self._clock() - started_at) * 1000, 3)
        log_event(
            logger,
            "takeover_routed",
            request_id=request_id,
            session_id=memory.session_id,
            ownership_state=state.ownership_state.value,
            assigned_operator_id=state.assigned_operator_id,
            latency_ms=latency_ms,
            latency_target_ms=100,
            over_latency_target=latency_ms > 100,
        )
        return TakeoverTransition(
            session_id=memory.session_id,
            ownership_state=state.ownership_state,
            transition_message=_localized_handoff_message(language, state.ownership_state),
            handoff_payload=handoff,
            assigned_operator_id=state.assigned_operator_id,
            escalation_reason=reason,
            queue_wait_ms=(
                active_item.assignment.queue_wait_ms
                if active_item.assignment is not None
                else None
            ),
        )

    def build_handoff_payload(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None,
        reason: str,
        workflow_state: str,
        request_id: str | None = None,
    ) -> HumanHandoffPayload:
        started_at = self._clock()
        language = language or default_language_snapshot()
        log_event(
            logger,
            "handoff_started",
            request_id=request_id,
            session_id=memory.session_id,
            escalation_reason=reason,
            workflow_state=workflow_state,
        )
        snapshot = memory.runtime_memory.snapshot()
        handoff = HumanHandoffPayload(
            session_id=memory.session_id,
            caller_identity={
                "name": snapshot.caller.name,
                "phone_number": snapshot.caller.phone_number,
            },
            booking_progress={
                "stage": snapshot.booking.stage,
                "captured": dict(snapshot.booking.values),
                "pending_fields": list(snapshot.booking.pending_fields),
                "awaiting_confirmation": snapshot.booking.awaiting_confirmation,
                "confirmation_completed": snapshot.booking.confirmation_completed,
            },
            unresolved_issues=tuple(
                {
                    "field_name": question.field_name,
                    "attempts": question.attempts,
                    "last_turn": question.last_turn,
                }
                for question in snapshot.unresolved_questions
            ),
            active_language=language.active_language,
            language_note=_language_note(language),
            escalation_reason=reason,
            workflow_state=workflow_state,
            memory_summary=memory.runtime_memory.build_injection(max_chars=self._handoff_max_chars),
            correction_history={
                "active_fields": list(snapshot.correction.active_fields),
                "corrected_fields": dict(snapshot.correction.corrected_fields),
                "last_correction_turn": snapshot.correction.last_correction_turn,
            },
            generated_turn=snapshot.turn_index,
        )
        latency_ms = round((self._clock() - started_at) * 1000, 3)
        log_event(
            logger,
            "handoff_completed",
            request_id=request_id,
            session_id=memory.session_id,
            handoff_chars=len(handoff.memory_summary),
            unresolved_issue_count=len(handoff.unresolved_issues),
            latency_ms=latency_ms,
            latency_target_ms=200,
            over_latency_target=latency_ms > 200,
        )
        return handoff

    def resume_ai(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        human_resolution: dict[str, Any] | None = None,
        reason: str = "human_resolved",
        request_id: str | None = None,
    ) -> TakeoverTransition:
        language = language or default_language_snapshot()
        state = self._state_for(memory.session_id)
        if human_resolution:
            _apply_human_resolution(memory, human_resolution, request_id=request_id)
        self._queue.release_assignment(memory.session_id, completed=False, request_id=request_id)
        previous_started_at = state.takeover_started_at
        state.ownership_state = TakeoverOwnershipState.AI_RESUMED
        state.assigned_operator_id = None
        state.last_recovery_reason = reason
        state.state_started_at = self._clock()
        duration_ms = (
            round((self._clock() - previous_started_at) * 1000, 3)
            if previous_started_at is not None
            else 0.0
        )
        log_event(
            logger,
            "ai_resumed",
            request_id=request_id,
            session_id=memory.session_id,
            recovery_reason=reason,
            workflow_stage=memory.booking_stage.value,
            active_language=language.active_language,
        )
        log_event(
            logger,
            "takeover_duration",
            request_id=request_id,
            session_id=memory.session_id,
            takeover_duration=duration_ms,
            takeover_duration_ms=duration_ms,
        )
        return TakeoverTransition(
            session_id=memory.session_id,
            ownership_state=state.ownership_state,
            transition_message=_localized_ai_resumed_message(language),
            handoff_payload=state.handoff_payload,
            assigned_operator_id=None,
            escalation_reason=state.escalation_reason,
            recovery_reason=reason,
        )

    def handle_supervisor_disconnect(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
    ) -> TakeoverTransition:
        language = language or default_language_snapshot()
        state = self._state_for(memory.session_id)
        previous_operator = state.assigned_operator_id
        self._queue.release_assignment(memory.session_id, completed=False, request_id=request_id)
        log_event(
            logger,
            "supervisor_disconnect",
            request_id=request_id,
            session_id=memory.session_id,
            operator_id=previous_operator,
        )
        if state.handoff_payload is None:
            return self._recover_to_ai(
                memory=memory,
                language=language,
                reason="handoff_missing_after_disconnect",
                request_id=request_id,
                handoff=None,
            )
        item = self._queue.enqueue(
            state.handoff_payload,
            priority=EscalationPriority.HIGH,
            request_id=request_id,
        )
        if item.status == EscalationQueueStatus.OVERFLOWED:
            return self._recover_to_ai(
                memory=memory,
                language=language,
                reason="queue_overflow_after_disconnect",
                request_id=request_id,
                handoff=state.handoff_payload,
            )
        assigned_item = self._queue.assign_next(request_id=request_id)
        active_item = self._queue.get(memory.session_id) or item
        if assigned_item is not None and assigned_item.session_id == memory.session_id:
            active_item = assigned_item
        if active_item.status == EscalationQueueStatus.ASSIGNED and active_item.assignment:
            state.ownership_state = TakeoverOwnershipState.HUMAN_ACTIVE
            state.assigned_operator_id = active_item.assignment.operator_id
            state.takeover_started_at = active_item.assignment.assigned_at
        else:
            state.ownership_state = TakeoverOwnershipState.WAITING_FOR_HUMAN
            state.assigned_operator_id = None
        return TakeoverTransition(
            session_id=memory.session_id,
            ownership_state=state.ownership_state,
            transition_message=_localized_handoff_message(language, state.ownership_state),
            handoff_payload=state.handoff_payload,
            assigned_operator_id=state.assigned_operator_id,
            escalation_reason=state.escalation_reason,
        )

    def recover_timed_out_escalations(
        self,
        *,
        request_id: str | None = None,
    ) -> tuple[TakeoverTransition, ...]:
        transitions: list[TakeoverTransition] = []
        for item in self._queue.recover_timeouts(request_id=request_id):
            state = self._state_for(item.session_id)
            state.ownership_state = TakeoverOwnershipState.AI_RESUMED
            state.last_recovery_reason = "escalation_timeout"
            state.state_started_at = self._clock()
            log_event(
                logger,
                "ai_resumed",
                request_id=request_id,
                session_id=item.session_id,
                recovery_reason="escalation_timeout",
                workflow_stage=item.payload.workflow_state,
                active_language=item.payload.active_language,
            )
            transitions.append(
                TakeoverTransition(
                    session_id=item.session_id,
                    ownership_state=TakeoverOwnershipState.AI_RESUMED,
                    transition_message="I will continue helping while the clinic team is unavailable.",
                    handoff_payload=item.payload,
                    assigned_operator_id=None,
                    escalation_reason=item.payload.escalation_reason,
                    recovery_reason="escalation_timeout",
                )
            )
        return tuple(transitions)

    def complete_call(self, *, session_id: str, request_id: str | None = None) -> None:
        state = self._state_for(session_id)
        self._queue.release_assignment(session_id, completed=True, request_id=request_id)
        state.ownership_state = TakeoverOwnershipState.CALL_COMPLETED
        state.state_started_at = self._clock()
        log_event(
            logger,
            "takeover_call_completed",
            request_id=request_id,
            session_id=session_id,
            escalation_reason=state.escalation_reason,
        )

    def snapshot(self, session_id: str) -> TakeoverRuntimeSnapshot:
        state = self._state_for(session_id)
        item = self._queue.get(session_id)
        return TakeoverRuntimeSnapshot(
            session_id=session_id,
            ownership_state=state.ownership_state,
            escalation_reason=state.escalation_reason,
            assigned_operator_id=state.assigned_operator_id,
            queued=item is not None and item.status == EscalationQueueStatus.PENDING,
            queue_position=self._queue.queue_position(session_id),
            pending_count=self._queue.pending_count,
            active_human_count=self._queue.active_human_count,
            last_recovery_reason=state.last_recovery_reason,
        )

    def _policy_decision(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        faq_answer_exists: bool,
        unsupported_request: bool,
        interruption_count: int,
    ) -> EscalationPolicyDecision:
        if _EMERGENCY_RE.search(transcript):
            return EscalationPolicyDecision(True, "emergency", EscalationPriority.EMERGENCY, "text")
        if _ESCALATION_REQUEST_RE.search(transcript) or memory.escalation_triggered:
            return EscalationPolicyDecision(
                True,
                "caller_escalation_request",
                EscalationPriority.HIGH,
                "text",
            )
        if _FRUSTRATION_RE.search(transcript):
            return EscalationPolicyDecision(
                True,
                "caller_frustration",
                EscalationPriority.HIGH,
                "text",
            )
        if faq_answer_exists:
            return EscalationPolicyDecision(False, None, EscalationPriority.LOW, "faq_available")
        if _SIMPLE_CORRECTION_RE.search(transcript) and memory.booking_values():
            return EscalationPolicyDecision(False, None, EscalationPriority.LOW, "simple_correction")
        if unsupported_request or _UNSUPPORTED_RE.search(transcript):
            return EscalationPolicyDecision(
                True,
                "unsupported_request",
                EscalationPriority.NORMAL,
                "policy",
            )
        if interruption_count >= 4:
            return EscalationPolicyDecision(
                True,
                "repeated_interruptions",
                EscalationPriority.NORMAL,
                "runtime",
            )
        if any(count >= 3 for count in memory.booking.retry_counts.values()):
            return EscalationPolicyDecision(
                True,
                "booking_failure_loop",
                EscalationPriority.HIGH,
                "runtime",
            )
        if any(question.attempts >= 3 for question in memory.runtime_memory.unresolved_questions()):
            return EscalationPolicyDecision(
                True,
                "repeated_unresolved_issue",
                EscalationPriority.NORMAL,
                "runtime_memory",
            )
        if language.confidence < 0.35:
            return EscalationPolicyDecision(
                True,
                "low_confidence_state",
                EscalationPriority.NORMAL,
                "language_router",
            )
        return EscalationPolicyDecision(False, None, EscalationPriority.LOW, "none")

    def _recover_to_ai(
        self,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot,
        reason: str,
        request_id: str | None,
        handoff: HumanHandoffPayload | None,
    ) -> TakeoverTransition:
        state = self._state_for(memory.session_id)
        state.ownership_state = TakeoverOwnershipState.AI_RESUMED
        state.assigned_operator_id = None
        state.last_recovery_reason = reason
        state.state_started_at = self._clock()
        log_event(
            logger,
            "ai_resumed",
            request_id=request_id,
            session_id=memory.session_id,
            recovery_reason=reason,
            workflow_stage=memory.booking_stage.value,
            active_language=language.active_language,
        )
        return TakeoverTransition(
            session_id=memory.session_id,
            ownership_state=TakeoverOwnershipState.AI_RESUMED,
            transition_message=_localized_ai_resumed_message(language),
            handoff_payload=handoff,
            assigned_operator_id=None,
            escalation_reason=state.escalation_reason,
            recovery_reason=reason,
        )

    def _state_for(self, session_id: str) -> _TakeoverSessionState:
        state = self._states.get(session_id)
        if state is None:
            state = _TakeoverSessionState(session_id=session_id, state_started_at=self._clock())
            self._states[session_id] = state
        return state


def _apply_human_resolution(
    memory: CallSessionMemory,
    resolution: dict[str, Any],
    *,
    request_id: str | None,
) -> None:
    booking_updates = resolution.get("booking_updates") or {}
    if not isinstance(booking_updates, dict):
        booking_updates = {}
    corrected: list[str] = []
    captured: dict[str, str] = {}
    for field_name, value in booking_updates.items():
        attr = _FIELD_TO_ATTR.get(str(field_name))
        if attr is None or value is None:
            continue
        text = _clean(str(value))
        if not text:
            continue
        current = getattr(memory.booking, attr)
        setattr(memory.booking, attr, text)
        captured[str(field_name)] = text
        if current is not None and current != text:
            corrected.append(str(field_name))
    if captured:
        memory.booking.active_correction = ",".join(corrected) if corrected else None
        memory.booking.awaiting_confirmation = False
        memory.booking.confirmation_completed = False
        memory.booking_stage = booking_stage_from_pending_fields(memory.pending_booking_fields)
        memory.runtime_memory.note_corrections(tuple(corrected), request_id=request_id)
        memory.runtime_memory.update_from_session(memory, request_id=request_id)
        log_event(
            logger,
            "human_resolution_applied",
            request_id=request_id,
            session_id=memory.session_id,
            updated_booking_fields=list(captured),
            corrected_fields=corrected,
            workflow_stage=memory.booking_stage.value,
        )

    resolved_fields = resolution.get("resolved_fields") or ()
    for field_name in resolved_fields:
        if str(field_name) in {field.value for field in BookingField}:
            memory.runtime_memory.resolve_question(field_name=str(field_name), request_id=request_id)


def _operator_supports_language(operator: HumanOperator, language: str) -> bool:
    if language in operator.languages:
        return True
    if language == "mixed" and any(item in operator.languages for item in ("hindi", "english")):
        return True
    if language == "hinglish" and any(item in operator.languages for item in ("hindi", "hinglish")):
        return True
    return False


def _language_note(language: SessionLanguageSnapshot) -> str:
    active = LANGUAGE_LABELS.get(language.active_language, language.active_language.title())
    parts = [f"Preferred Language: {active}"]
    if language.active_language == "hinglish":
        parts.append("Caller mixes Hindi and English naturally.")
    elif language.active_language == "mixed":
        dominant = LANGUAGE_LABELS.get(language.dominant_language, language.dominant_language.title())
        parts.append(f"Caller uses mixed language; keep {dominant} as the anchor.")
    elif language.previous_language and language.previous_language != language.active_language:
        previous = LANGUAGE_LABELS.get(language.previous_language, language.previous_language.title())
        parts.append(f"Caller occasionally mixes {previous}.")
    return " ".join(parts)


def _localized_handoff_message(
    language: SessionLanguageSnapshot,
    ownership_state: TakeoverOwnershipState,
) -> str:
    if ownership_state == TakeoverOwnershipState.WAITING_FOR_HUMAN:
        if language.active_language in {"hindi", "hinglish", "mixed"}:
            return "Main clinic coordinator ko connect kar raha hoon. Ek moment please."
        return "Let me connect you with our clinic coordinator. One moment please."
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        return "Main aapko clinic coordinator se connect kar raha hoon."
    return "Let me connect you with our clinic coordinator."


def _localized_ai_resumed_message(language: SessionLanguageSnapshot) -> str:
    if language.active_language in {"hindi", "hinglish", "mixed"}:
        return "Main yahin hoon. Hum wahi se continue karte hain."
    return "I am still here. We can continue from where we left off."


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _safe_signal(text: str) -> str:
    if _EMERGENCY_RE.search(text):
        return "emergency"
    if _ESCALATION_REQUEST_RE.search(text):
        return "escalation_request"
    if _FRUSTRATION_RE.search(text):
        return "frustration"
    if _UNSUPPORTED_RE.search(text):
        return "unsupported"
    return "none"
