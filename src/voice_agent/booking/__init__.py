from __future__ import annotations

from voice_agent.booking.entities import (
    ALL_BOOKING_FIELDS,
    OPTIONAL_BOOKING_FIELDS,
    REQUIRED_BOOKING_FIELDS,
    BookingConfidenceResult,
    BookingCorrectionDecision,
    BookingExtraction,
    BookingField,
    BookingFieldValue,
    BookingIssue,
    BookingStateSnapshot,
    BookingValidationResult,
)
from voice_agent.booking.legacy import (
    BOOKING_FAILURE_RESPONSE,
    BookingCalendar,
    BookingConfirmationEvent,
    BookingDetails,
    BookingTurnResult,
)
from voice_agent.booking.runtime import BookingRuntimeResult
from voice_agent.booking.utils import TimePreference
from voice_agent.validation import RuntimeIntegrityValidator


def __getattr__(name: str):
    if name in {"BookingIntelligenceWorkflow", "BookingWorkflowResult"}:
        from voice_agent.booking.workflow import (
            BookingIntelligenceWorkflow,
            BookingWorkflowResult,
        )

        return {
            "BookingIntelligenceWorkflow": BookingIntelligenceWorkflow,
            "BookingWorkflowResult": BookingWorkflowResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ALL_BOOKING_FIELDS",
    "BOOKING_FAILURE_RESPONSE",
    "OPTIONAL_BOOKING_FIELDS",
    "REQUIRED_BOOKING_FIELDS",
    "BookingCalendar",
    "BookingConfidenceResult",
    "BookingConfirmationEvent",
    "BookingCorrectionDecision",
    "BookingDetails",
    "BookingExtraction",
    "BookingField",
    "BookingFieldValue",
    "BookingIssue",
    "BookingRuntimeResult",
    "BookingStateSnapshot",
    "BookingTurnResult",
    "BookingValidationResult",
    "BookingIntelligenceWorkflow",
    "BookingWorkflowResult",
    "RuntimeIntegrityValidator",
    "TimePreference",
]
