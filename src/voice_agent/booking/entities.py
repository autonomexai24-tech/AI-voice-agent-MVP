from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class BookingField(str, Enum):
    CUSTOMER_NAME = "customer_name"
    PHONE_NUMBER = "phone_number"
    SERVICE_TYPE = "service_type"
    APPOINTMENT_DATE = "appointment_date"
    APPOINTMENT_TIME = "appointment_time"
    DOCTOR_PREFERENCE = "doctor_preference"
    NOTES = "notes"
    LANGUAGE = "language"


REQUIRED_BOOKING_FIELDS: tuple[BookingField, ...] = (
    BookingField.CUSTOMER_NAME,
    BookingField.PHONE_NUMBER,
    BookingField.SERVICE_TYPE,
    BookingField.APPOINTMENT_DATE,
    BookingField.APPOINTMENT_TIME,
    BookingField.DOCTOR_PREFERENCE,
)
OPTIONAL_BOOKING_FIELDS: tuple[BookingField, ...] = (BookingField.NOTES,)
ALL_BOOKING_FIELDS: tuple[BookingField, ...] = (
    *REQUIRED_BOOKING_FIELDS,
    *OPTIONAL_BOOKING_FIELDS,
    BookingField.LANGUAGE,
)


@dataclass(frozen=True)
class BookingFieldValue:
    field: BookingField
    value: str
    confidence: float
    source_text: str
    language: str
    corrected: bool = False


@dataclass(frozen=True)
class BookingIssue:
    code: str
    field: BookingField | None
    message: str
    value: str | None = None
    severity: str = "warning"


@dataclass(frozen=True)
class BookingExtraction:
    values: tuple[BookingFieldValue, ...]
    issues: tuple[BookingIssue, ...]
    raw_text: str
    language: str

    def by_field(self) -> dict[BookingField, BookingFieldValue]:
        fields: dict[BookingField, BookingFieldValue] = {}
        for value in self.values:
            fields[value.field] = value
        return fields


@dataclass(frozen=True)
class BookingValidationResult:
    accepted_values: tuple[BookingFieldValue, ...]
    issues: tuple[BookingIssue, ...]

    @property
    def has_blocking_issue(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


@dataclass(frozen=True)
class BookingConfidenceResult:
    is_low_confidence: bool
    issue: BookingIssue | None = None


@dataclass(frozen=True)
class BookingCorrectionDecision:
    is_correction: bool
    fields: tuple[BookingField, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class BookingStateSnapshot:
    values: Mapping[BookingField, str]
    pending_required_fields: tuple[BookingField, ...]
    pending_optional_fields: tuple[BookingField, ...]
    awaiting_confirmation: bool
    confirmation_completed: bool
    stage: str
    language: str

    @property
    def required_complete(self) -> bool:
        return not self.pending_required_fields

    @property
    def complete(self) -> bool:
        return self.required_complete and not self.pending_optional_fields


def normalize_field_name(field: BookingField | str) -> BookingField:
    if isinstance(field, BookingField):
        return field
    if field == "optional_notes":
        return BookingField.NOTES
    return BookingField(field)


def external_field_name(field: BookingField) -> str:
    return field.value
