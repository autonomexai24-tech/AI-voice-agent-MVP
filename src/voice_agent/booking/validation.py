from __future__ import annotations

from datetime import date
import re

from voice_agent.booking.entities import (
    BookingExtraction,
    BookingField,
    BookingFieldValue,
    BookingIssue,
    BookingValidationResult,
)


_DATE_ISO_RE = re.compile(r"^(20\d{2})-(\d{1,2})-(\d{1,2})$")
_DATE_SLASH_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?$")
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def validate_booking_extraction(
    extraction: BookingExtraction,
    *,
    known_values: dict[BookingField, str],
    services: tuple[str, ...],
    correction_fields: tuple[BookingField, ...] = (),
    current_date: date | None = None,
) -> BookingValidationResult:
    issues: list[BookingIssue] = list(extraction.issues)
    blocked_fields = {
        issue.field
        for issue in issues
        if issue.severity == "error" and issue.field is not None
    }
    accepted: list[BookingFieldValue] = []

    for value in extraction.values:
        if value.field in blocked_fields:
            continue
        if value.field == BookingField.LANGUAGE:
            accepted.append(value)
            continue

        service_value = value
        if value.field == BookingField.SERVICE_TYPE:
            mapped_service = _canonical_service(value.value, services)
            if services and mapped_service is None:
                issues.append(
                    BookingIssue(
                        code="unsupported_service",
                        field=BookingField.SERVICE_TYPE,
                        message="Requested service is not configured.",
                        value=value.value,
                        severity="error",
                    )
                )
                continue
            if mapped_service is not None and mapped_service != value.value:
                service_value = BookingFieldValue(
                    field=value.field,
                    value=mapped_service,
                    confidence=max(value.confidence, 0.86),
                    source_text=value.source_text,
                    language=value.language,
                    corrected=value.corrected,
                )

        if value.field == BookingField.APPOINTMENT_DATE:
            date_issue = _validate_date_value(value.value, current_date)
            if date_issue is not None:
                issues.append(date_issue)
                continue

        existing = known_values.get(value.field)
        if (
            existing is not None
            and _normalize(existing) != _normalize(service_value.value)
            and value.field not in correction_fields
        ):
            issues.append(
                BookingIssue(
                    code="conflicting_booking_value",
                    field=value.field,
                    message="New value conflicts with the current booking state.",
                    value=service_value.value,
                    severity="error",
                )
            )
            continue
        accepted.append(service_value)

    return BookingValidationResult(
        accepted_values=tuple(accepted),
        issues=tuple(issues),
    )


def _canonical_service(value: str, services: tuple[str, ...]) -> str | None:
    if not services:
        return value
    normalized = _normalize(value)
    for service in services:
        service_norm = _normalize(service)
        if normalized == service_norm:
            return service
        if normalized in service_norm or service_norm in normalized:
            return service
    value_tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for service in services:
        service_tokens = set(re.findall(r"[a-z0-9]+", _normalize(service)))
        if value_tokens and value_tokens <= service_tokens:
            return service
    return None


def _validate_date_value(value: str, current_date: date | None) -> BookingIssue | None:
    parsed = _parse_absolute_date(value, current_date)
    if parsed is None:
        if _looks_absolute_date(value):
            return BookingIssue(
                code="invalid_date",
                field=BookingField.APPOINTMENT_DATE,
                message="Appointment date is invalid.",
                value=value,
                severity="error",
            )
        return None
    if current_date is not None and parsed < current_date:
        return BookingIssue(
            code="past_date",
            field=BookingField.APPOINTMENT_DATE,
            message="Appointment date is in the past.",
            value=value,
            severity="error",
        )
    return None


def _parse_absolute_date(value: str, current_date: date | None) -> date | None:
    normalized = value.strip().lower()
    match = _DATE_ISO_RE.match(normalized)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DATE_SLASH_RE.match(normalized)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or (current_date or date.today()).year)
        return _safe_date(year, month, day)

    month_match = re.match(
        r"^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)$",
        normalized,
    )
    if month_match:
        month = _MONTHS.get(month_match.group(2))
        if month is None:
            return None
        year = (current_date or date.today()).year
        return _safe_date(year, month, int(month_match.group(1)))

    reverse_month_match = re.match(
        r"^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$",
        normalized,
    )
    if reverse_month_match:
        month = _MONTHS.get(reverse_month_match.group(1))
        if month is None:
            return None
        year = (current_date or date.today()).year
        return _safe_date(year, month, int(reverse_month_match.group(2)))
    return None


def _looks_absolute_date(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(
        _DATE_ISO_RE.match(normalized)
        or _DATE_SLASH_RE.match(normalized)
        or re.match(r"^\d{1,2}(?:st|nd|rd|th)?\s+[a-z]+$", normalized)
        or re.match(r"^[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?$", normalized)
    )


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
