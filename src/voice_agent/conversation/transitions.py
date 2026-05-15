from __future__ import annotations

from voice_agent.conversation.states import ConversationState

_ALLOWED_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.IDLE: frozenset(
        {
            ConversationState.GREETING,
            ConversationState.LISTENING,
            ConversationState.ENDED,
        }
    ),
    ConversationState.GREETING: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.INTERRUPTED,
            ConversationState.ENDED,
        }
    ),
    ConversationState.LISTENING: frozenset(
        {
            ConversationState.THINKING,
            ConversationState.BOOKING_DISCOVERY,
            ConversationState.BOOKING_CONFIRMATION,
            ConversationState.SPEAKING,
            ConversationState.ESCALATION,
            ConversationState.CLOSING,
            ConversationState.ENDED,
        }
    ),
    ConversationState.THINKING: frozenset(
        {
            ConversationState.SPEAKING,
            ConversationState.LISTENING,
            ConversationState.BOOKING_DISCOVERY,
            ConversationState.BOOKING_CONFIRMATION,
            ConversationState.INTERRUPTED,
            ConversationState.ESCALATION,
            ConversationState.ENDED,
        }
    ),
    ConversationState.SPEAKING: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.INTERRUPTED,
            ConversationState.CLOSING,
            ConversationState.ENDED,
        }
    ),
    ConversationState.BOOKING_DISCOVERY: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.BOOKING_CONFIRMATION,
            ConversationState.INTERRUPTED,
            ConversationState.ENDED,
        }
    ),
    ConversationState.BOOKING_CONFIRMATION: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.CLOSING,
            ConversationState.INTERRUPTED,
            ConversationState.ENDED,
        }
    ),
    ConversationState.INTERRUPTED: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.BOOKING_DISCOVERY,
            ConversationState.ENDED,
        }
    ),
    ConversationState.ESCALATION: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.CLOSING,
            ConversationState.ENDED,
        }
    ),
    ConversationState.CLOSING: frozenset({ConversationState.ENDED}),
    ConversationState.ENDED: frozenset(),
}


def can_transition(
    current_state: ConversationState,
    next_state: ConversationState,
) -> bool:
    if current_state == next_state:
        return True
    return next_state in _ALLOWED_TRANSITIONS[current_state]
