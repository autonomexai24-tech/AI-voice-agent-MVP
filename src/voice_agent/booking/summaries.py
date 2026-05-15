from __future__ import annotations

from dataclasses import dataclass
import hashlib

from voice_agent.booking.entities import BookingField


@dataclass(frozen=True)
class BookingSummary:
    text: str
    fingerprint: str


def build_booking_summary(values: dict[BookingField, str]) -> BookingSummary:
    service = _display(values.get(BookingField.SERVICE_TYPE, "the visit"))
    date = _display_date(values.get(BookingField.APPOINTMENT_DATE, "the preferred date"))
    time = values.get(BookingField.APPOINTMENT_TIME, "the preferred time")
    doctor = values.get(BookingField.DOCTOR_PREFERENCE)
    name = values.get(BookingField.CUSTOMER_NAME, "the caller")

    doctor_part = ""
    if doctor:
        doctor_part = f" with {doctor}"
    date_time = f"{date} {time}" if _is_daypart(time) else f"{date} at {time}"
    text = f"Just confirming: {service} {date_time}{doctor_part} for {name}. Is that correct?"
    fingerprint = hashlib.sha256(_summary_key(values).encode("utf-8")).hexdigest()[:16]
    return BookingSummary(text=text, fingerprint=fingerprint)


def _summary_key(values: dict[BookingField, str]) -> str:
    fields = (
        BookingField.CUSTOMER_NAME,
        BookingField.PHONE_NUMBER,
        BookingField.SERVICE_TYPE,
        BookingField.APPOINTMENT_DATE,
        BookingField.APPOINTMENT_TIME,
        BookingField.DOCTOR_PREFERENCE,
        BookingField.NOTES,
    )
    return "|".join(f"{field.value}={values.get(field, '')}" for field in fields)


def _display(value: str) -> str:
    if not value:
        return value
    if value.lower().startswith("dr."):
        return value
    return value[:1].upper() + value[1:]


def _display_date(value: str) -> str:
    if value.lower() in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
        return value.title()
    return value


def _is_daypart(value: str) -> bool:
    return value.lower() in {"morning", "afternoon", "evening", "noon"}
