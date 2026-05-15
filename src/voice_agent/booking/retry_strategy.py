from __future__ import annotations

from voice_agent.booking.entities import BookingField, BookingIssue


def prompt_for_missing_field(
    field: BookingField,
    *,
    language: str,
    services: tuple[str, ...] = (),
) -> str:
    if field == BookingField.CUSTOMER_NAME:
        return _localized(language, "Sure. May I have your name?", "Sure. Aapka naam bata dijiye?")
    if field == BookingField.PHONE_NUMBER:
        return _localized(language, "And your phone number, please?", "Aur phone number bata dijiye?")
    if field == BookingField.SERVICE_TYPE:
        suffix = _service_suffix(services)
        return _localized(
            language,
            f"Which service is this for{suffix}?",
            f"Kaunsi service ke liye{suffix}?",
        )
    if field == BookingField.APPOINTMENT_DATE:
        return _localized(language, "Which date would you prefer?", "Kaunsi date chahiye?")
    if field == BookingField.APPOINTMENT_TIME:
        return _localized(language, "What time would you prefer?", "Kaunsa time theek rahega?")
    if field == BookingField.DOCTOR_PREFERENCE:
        return _localized(
            language,
            "Do you have a doctor preference, or is any doctor okay?",
            "Koi doctor preference hai, ya any doctor theek hai?",
        )
    return _localized(
        language,
        "Any notes I should add for the visit?",
        "Visit ke liye koi notes add karna hai?",
    )


def prompt_for_issue(
    issue: BookingIssue,
    *,
    language: str,
    attempt: int,
    services: tuple[str, ...] = (),
) -> str:
    field = issue.field
    if issue.code == "invalid_time" or field == BookingField.APPOINTMENT_TIME:
        if issue.code == "ambiguous_time":
            return _localized(
                language,
                "I may have misunderstood the time. Did you mean AM or PM?",
                "Time clear nahi hua. AM ya PM bata dijiye?",
            )
        return _localized(
            language,
            "I may have misunderstood the appointment time. Could you repeat the preferred time?",
            "Appointment time clear nahi hua. Preferred time dobara bata dijiye?",
        )
    if issue.code in {"invalid_date", "past_date"} or field == BookingField.APPOINTMENT_DATE:
        return _localized(
            language,
            "I may have misunderstood the date. Which date would you prefer?",
            "Date clear nahi hui. Kaunsi date chahiye?",
        )
    if issue.code == "unsupported_service":
        suffix = _service_suffix(services)
        return _localized(
            language,
            f"I do not see that service listed here. Which service should I book{suffix}?",
            f"Ye service list mein nahi dikh rahi. Kaunsi service book karun{suffix}?",
        )
    if issue.code.startswith("conflicting"):
        target = _field_label(field)
        return _localized(
            language,
            f"I heard two {target} details. Could you confirm the correct one?",
            f"{target} ke do details sunai diye. Correct wala bata dijiye?",
        )
    if issue.code == "noisy_transcript":
        return _localized(
            language,
            "Sorry, the line was not clear. Could you repeat that detail?",
            "Sorry, line clear nahi thi. Detail dobara bata dijiye?",
        )
    if field is not None:
        return prompt_for_missing_field(field, language=language, services=services)
    if attempt > 1:
        return _localized(
            language,
            "Could you repeat that once more?",
            "Ek baar aur repeat kar dijiye?",
        )
    return _localized(language, "Could you repeat that?", "Dobara bata dijiye?")


def prompt_for_confirmation_retry(*, language: str) -> str:
    return _localized(
        language,
        "Is that correct, or should I change something?",
        "Ye correct hai, ya kuch change karna hai?",
    )


def prompt_for_silence(field: BookingField, *, language: str, services: tuple[str, ...]) -> str:
    prompt = prompt_for_missing_field(field, language=language, services=services)
    return _localized(
        language,
        f"No hurry. {prompt}",
        f"Koi jaldi nahi. {prompt}",
    )


def _field_label(field: BookingField | None) -> str:
    if field is None:
        return "booking"
    return field.value.replace("_", " ")


def _service_suffix(services: tuple[str, ...]) -> str:
    if not services:
        return ""
    if len(services) == 1:
        return f" - {services[0]}"
    return f" - {', '.join(services[:-1])}, or {services[-1]}"


def _localized(language: str, english: str, hinglish: str) -> str:
    if language in {"hindi", "hinglish", "mixed"}:
        return hinglish
    return english
