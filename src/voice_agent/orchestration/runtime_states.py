from __future__ import annotations

from enum import Enum


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


RUNTIME_ALLOWED_TRANSITIONS: dict[ConversationRuntimeState, frozenset[ConversationRuntimeState]] = {
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

ALL_RUNTIME_STATES = frozenset(ConversationRuntimeState)
