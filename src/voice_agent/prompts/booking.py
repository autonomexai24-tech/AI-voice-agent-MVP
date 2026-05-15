from __future__ import annotations

from typing import Protocol


class BookingMemoryView(Protocol):
    caller_name: str | None
    phone_number: str | None
    selected_service: str | None
    preferred_date: str | None
    preferred_time: str | None
    doctor_preference: str | None
    optional_notes: str | None
    pending_booking_fields: tuple[str, ...]


def booking_prompt(memory: BookingMemoryView | None = None) -> str:
    base = (
        "Booking flow:\n"
        "- If the caller wants an appointment, collect details conversationally.\n"
        "- Required booking fields: customer name, phone number, appointment date, appointment time, service type, doctor preference.\n"
        "- Optional field: notes for the visit.\n"
        "- Do not confirm real availability, final bookings, payments, or reminders.\n"
        "- When details are collected, summarize them and say the clinic team can confirm the slot.\n"
        "- Ask for only one missing field at a time."
    )
    if memory is None:
        return base

    fields = {
        "customer_name": memory.caller_name,
        "phone_number": memory.phone_number,
        "service_type": memory.selected_service,
        "appointment_date": memory.preferred_date,
        "appointment_time": memory.preferred_time,
        "doctor_preference": memory.doctor_preference,
        "optional_notes": memory.optional_notes,
    }
    captured = "; ".join(
        f"{name}={value}" for name, value in fields.items() if value not in (None, "")
    )
    pending = ", ".join(memory.pending_booking_fields) or "none"
    return (
        f"{base}\n"
        "Current in-call booking memory:\n"
        f"- Captured: {captured or 'none'}\n"
        f"- Pending: {pending}"
    )
