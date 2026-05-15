from __future__ import annotations

import re

from voice_agent.booking.entities import (
    BookingCorrectionDecision,
    BookingExtraction,
    BookingField,
)


_CORRECTION_RE = re.compile(
    r"\b(?:actually|instead|change|make it|rather|sorry|not that|no,|nope,|"
    r"galat|badal|badlo|illai|alla)\b",
    re.IGNORECASE,
)


def detect_booking_correction(
    transcript: str,
    extraction: BookingExtraction,
    *,
    known_values: dict[BookingField, str],
    awaiting_confirmation: bool,
) -> BookingCorrectionDecision:
    extracted_fields = tuple(
        value.field
        for value in extraction.values
        if value.field not in {BookingField.LANGUAGE, BookingField.NOTES}
    )
    if not extracted_fields:
        return BookingCorrectionDecision(False)

    has_cue = bool(_CORRECTION_RE.search(transcript))
    differs = tuple(
        field
        for field in extracted_fields
        if field in known_values
        and _normalize(known_values[field]) != _normalize(_value_for(field, extraction) or "")
    )

    if has_cue:
        return BookingCorrectionDecision(
            True,
            fields=extracted_fields,
            reason="explicit_correction_cue",
        )
    if awaiting_confirmation and differs:
        return BookingCorrectionDecision(
            True,
            fields=differs,
            reason="confirmation_correction",
        )
    return BookingCorrectionDecision(False)


def _value_for(field: BookingField, extraction: BookingExtraction) -> str | None:
    for value in extraction.values:
        if value.field == field:
            return value.value
    return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
