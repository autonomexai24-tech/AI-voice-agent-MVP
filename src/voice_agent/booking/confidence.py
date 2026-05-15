from __future__ import annotations

from voice_agent.booking.entities import (
    BookingConfidenceResult,
    BookingFieldValue,
    BookingIssue,
)


LOW_CONFIDENCE_THRESHOLD = 0.7
_LOW_CONFIDENCE_ISSUES = {"ambiguous_time", "noisy_transcript"}


def evaluate_booking_confidence(
    values: tuple[BookingFieldValue, ...],
    issues: tuple[BookingIssue, ...],
) -> BookingConfidenceResult:
    for issue in issues:
        if issue.code in _LOW_CONFIDENCE_ISSUES:
            return BookingConfidenceResult(True, issue)

    low_value = min(
        (value for value in values if value.confidence < LOW_CONFIDENCE_THRESHOLD),
        key=lambda value: value.confidence,
        default=None,
    )
    if low_value is None:
        return BookingConfidenceResult(False)

    return BookingConfidenceResult(
        True,
        BookingIssue(
            code="low_confidence_field",
            field=low_value.field,
            message="Extracted field confidence is low.",
            value=low_value.value,
        ),
    )
