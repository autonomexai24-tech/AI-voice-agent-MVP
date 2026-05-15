from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConversationState(str, Enum):
    IDLE = "idle"
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    BOOKING_DISCOVERY = "booking_discovery"
    BOOKING_CONFIRMATION = "booking_confirmation"
    INTERRUPTED = "interrupted"
    ESCALATION = "escalation"
    CLOSING = "closing"
    ENDED = "ended"


class BookingStage(str, Enum):
    IDLE = "idle"
    CUSTOMER_COLLECTION = "customer_collection"
    PHONE_COLLECTION = "phone_collection"
    SERVICE_COLLECTION = "service_collection"
    DATE_COLLECTION = "date_collection"
    TIME_COLLECTION = "time_collection"
    DOCTOR_COLLECTION = "doctor_collection"
    NOTES_COLLECTION = "notes_collection"
    CONFIRMATION = "confirmation"


class PendingAction(str, Enum):
    NONE = "none"
    WAIT_FOR_CALLER = "wait_for_caller"
    GENERATE_RESPONSE = "generate_response"
    PLAY_RESPONSE = "play_response"
    ASK_BOOKING_FIELD = "ask_booking_field"
    RECOVER_FROM_INTERRUPTION = "recover_from_interruption"
    SILENCE_REPROMPT = "silence_reprompt"
    CLOSE_CALL = "close_call"


@dataclass(frozen=True)
class ConversationSnapshot:
    current_state: ConversationState
    previous_state: ConversationState | None
    booking_stage: BookingStage
    pending_action: PendingAction
    caller_speaking: bool
    ai_speaking: bool
    state_generation: int
