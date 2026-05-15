from __future__ import annotations

import re
from dataclasses import dataclass

from voice_agent.booking.entities import (
    BookingExtraction,
    BookingField,
    BookingFieldValue,
    BookingIssue,
)


_BOOKING_INTENT_RE = re.compile(
    r"\b(?:appointment|book|booking|schedule|visit|consultation|consult|come in|"
    r"dikhana|milna|chahiye|चाहिए|beku|venum|kaavali|venam)\b",
    re.IGNORECASE,
)
_NEED_WANT_RE = re.compile(r"\b(?:need|want|looking for)\b", re.IGNORECASE)
_SERVICE_LIKE_RE = re.compile(
    r"\b(?:treatment|cleaning|braces|canal|consultation|spa|therapy|checkup|service)\b",
    re.IGNORECASE,
)
_PHONE_CANDIDATE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{6,}\d)")
_TIME_12H_CANDIDATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)
_TIME_24H_CANDIDATE_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_AMBIGUOUS_HOUR_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})\s*(?:o'clock|oclock|baje)\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{1,2}-\d{1,2}\b")
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]20\d{2})?\b")
_MONTH_DATE_RE = re.compile(
    r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+"
    r"\d{1,2}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)
_NAME_PATTERNS = (
    re.compile(
        r"\b(?:my name is|name is|this is|i am|i'm|mera naam|main)\s+(.{2,80})",
        re.IGNORECASE,
    ),
)
_FOR_NAME_RE = re.compile(
    r"\b(?:for|under)\s+([A-Za-z][A-Za-z .'-]{1,60})\s*(?:sir|madam|please)?[.?!]?$",
    re.IGNORECASE,
)
_SERVICE_PATTERNS = (
    re.compile(
        r"\b(?:for|service is|need|want|looking for|chahiye|beku|venum|kaavali)\s+(.{3,90})",
        re.IGNORECASE,
    ),
)
_DOCTOR_RE = re.compile(r"\b(?:dr\.?|doctor)\s+([A-Za-z][A-Za-z .'-]{1,50})", re.I)
_NOTES_RE = re.compile(
    r"\b(?:note|notes|add|problem is|issue is|pain is)\s+(.{2,140})",
    re.IGNORECASE,
)
_NO_NOTES_RE = re.compile(r"\b(?:no|none|nothing|no notes|kuch nahi|nahi)\b", re.I)
_ANY_DOCTOR_RE = re.compile(
    r"\b(?:any doctor|anyone|no preference|koi bhi|any is fine|any doctor is fine)\b",
    re.IGNORECASE,
)
_YES_RE = re.compile(r"\b(?:yes|yeah|yep|correct|right|confirm|ok|okay|sure)\b", re.I)
_NO_RE = re.compile(r"\b(?:no|nope|wrong|incorrect|not correct)\b", re.I)
_NOISE_RE = re.compile(r"\b(?:inaudible|unclear|noise|cutting|distorted)\b|\[[^\]]+\]")

_DATE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("day after tomorrow", ("day after tomorrow",)),
    ("tomorrow", ("tomorrow",)),
    ("today", ("today",)),
    ("monday", ("monday",)),
    ("tuesday", ("tuesday",)),
    ("wednesday", ("wednesday",)),
    ("thursday", ("thursday",)),
    ("friday", ("friday",)),
    ("saturday", ("saturday",)),
    ("sunday", ("sunday",)),
    ("aaj", ("aaj", "आज")),
    ("kal", ("kal", "कल")),
    ("parso", ("parso", "परसों", "परसो")),
    ("nale", ("nale", "naale")),
    ("nalai", ("nalai", "naalai")),
    ("repu", ("repu",)),
)
_DATE_WORDS = tuple(term for _label, terms in _DATE_TERMS for term in terms)
_TIME_WORDS = {
    "morning": ("morning", 0.9),
    "सुबह": ("morning", 0.84),
    "subah": ("morning", 0.86),
    "afternoon": ("afternoon", 0.9),
    "dopahar": ("afternoon", 0.84),
    "evening": ("evening", 0.9),
    "शाम": ("evening", 0.84),
    "shaam": ("evening", 0.86),
    "sham": ("evening", 0.86),
    "sanje": ("evening", 0.82),
    "maalai": ("evening", 0.82),
    "sayantram": ("evening", 0.82),
    "noon": ("noon", 0.9),
}
_DATE_OR_TIME_STOPWORDS = {
    "today",
    "tomorrow",
    "morning",
    "afternoon",
    "evening",
    "noon",
    "aaj",
    "kal",
    "parso",
    "subah",
    "shaam",
    "sham",
    "dopahar",
}
_SERVICE_STOPWORDS = (
    "tomorrow",
    "today",
    "at",
    "doctor",
    "dr",
    "appointment",
    "booking",
    "please",
    "for",
    "on",
    "with",
    "my name",
    "phone",
    "number",
)


@dataclass(frozen=True)
class ExtractionContext:
    pending_fields: tuple[str, ...]
    services: tuple[str, ...]
    language: str
    known_values: dict[BookingField, str]


def has_booking_intent(transcript: str) -> bool:
    return bool(_BOOKING_INTENT_RE.search(transcript))


def looks_like_booking_request(transcript: str, services: tuple[str, ...]) -> bool:
    if has_booking_intent(transcript):
        return True
    if not _NEED_WANT_RE.search(transcript):
        return False
    if not _contains_date_or_time(transcript):
        return False
    normalized = transcript.lower()
    if any(service.lower() in normalized for service in services):
        return True
    return bool(_SERVICE_LIKE_RE.search(transcript))


def is_confirmation_yes(transcript: str) -> bool:
    return bool(_YES_RE.search(transcript))


def is_confirmation_no(transcript: str) -> bool:
    return bool(_NO_RE.search(transcript))


def extract_booking_entities(transcript: str, context: ExtractionContext) -> BookingExtraction:
    cleaned = _clean_text(transcript)
    issues: list[BookingIssue] = []
    values: list[BookingFieldValue] = []

    if _NOISE_RE.search(cleaned):
        issues.append(
            BookingIssue(
                code="noisy_transcript",
                field=None,
                message="Transcript appears noisy or partial.",
                value=cleaned[:80],
            )
        )

    for extractor in (
        _extract_phone,
        _extract_name,
        _extract_service,
        _extract_date,
        _extract_time,
        _extract_doctor,
        _extract_notes,
    ):
        value, field_issues = extractor(cleaned, context)
        issues.extend(field_issues)
        if value is not None:
            values.append(value)

    if context.language:
        values.append(
            BookingFieldValue(
                BookingField.LANGUAGE,
                context.language,
                1.0,
                cleaned,
                context.language,
            )
        )

    return BookingExtraction(
        values=tuple(values),
        issues=tuple(issues),
        raw_text=cleaned,
        language=context.language,
    )


def _extract_phone(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    match = _PHONE_CANDIDATE_RE.search(transcript)
    if not match:
        return None, ()
    raw = match.group(0)
    has_plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 15:
        return None, (
            BookingIssue(
                code="invalid_phone_number",
                field=BookingField.PHONE_NUMBER,
                message="Phone number length is invalid.",
                value=raw,
                severity="error",
            ),
        )
    if has_plus:
        value = f"+{digits}"
    elif len(digits) == 10:
        value = f"+91{digits}"
    else:
        value = digits
    return _field(BookingField.PHONE_NUMBER, value, 0.95, transcript, context), ()


def _extract_name(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(transcript)
        if match:
            name = _clean_name(match.group(1))
            if name:
                return _field(BookingField.CUSTOMER_NAME, name, 0.86, transcript, context), ()

    if "customer_name" not in context.pending_fields:
        return None, ()
    for_name = _FOR_NAME_RE.search(transcript)
    if for_name:
        candidate = for_name.group(1)
        if not _contains_date_or_time(candidate) and not _looks_like_service_or_doctor(candidate):
            name = _clean_name(candidate)
            if name:
                return _field(BookingField.CUSTOMER_NAME, name, 0.76, transcript, context), ()
    if _contains_date_or_time(transcript) or has_booking_intent(transcript):
        return None, ()
    if _PHONE_CANDIDATE_RE.search(transcript) or _looks_like_service_or_doctor(transcript):
        return None, ()
    if is_confirmation_yes(transcript) or is_confirmation_no(transcript):
        return None, ()
    if len(transcript.split()) > 5:
        return None, ()

    name = _clean_name(transcript)
    if name:
        return _field(BookingField.CUSTOMER_NAME, name, 0.72, transcript, context), ()
    return None, ()


def _extract_service(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    normalized = transcript.lower()
    matches = [service for service in context.services if service.lower() in normalized]
    if len(matches) > 1:
        return None, (
            BookingIssue(
                code="conflicting_service_values",
                field=BookingField.SERVICE_TYPE,
                message="Multiple service values were mentioned.",
                value=", ".join(matches),
                severity="error",
            ),
        )
    if len(matches) == 1:
        return _field(BookingField.SERVICE_TYPE, matches[0], 0.96, transcript, context), ()

    for pattern in _SERVICE_PATTERNS:
        match = pattern.search(transcript)
        if not match:
            continue
        candidate = _clean_service_candidate(match.group(1))
        if not candidate:
            continue
        if _looks_like_non_service(candidate):
            continue
        return _field(BookingField.SERVICE_TYPE, candidate.lower(), 0.68, transcript, context), ()
    return None, ()


def _extract_date(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    normalized = transcript.lower()
    found: list[str] = []
    occupied_spans: list[tuple[int, int]] = []
    for label, terms in _DATE_TERMS:
        for term in sorted(terms, key=len, reverse=True):
            for match in _term_matches(term, normalized):
                if _span_overlaps(match.span(), occupied_spans):
                    continue
                found.append(label)
                occupied_spans.append(match.span())

    for pattern in (_ISO_DATE_RE, _SLASH_DATE_RE, _MONTH_DATE_RE):
        for match in pattern.finditer(normalized):
            if _span_overlaps(match.span(), occupied_spans):
                continue
            found.append(match.group(0))
            occupied_spans.append(match.span())

    found = _dedupe(found)
    if len(found) > 1:
        return None, (
            BookingIssue(
                code="conflicting_date_values",
                field=BookingField.APPOINTMENT_DATE,
                message="Multiple appointment dates were mentioned.",
                value=", ".join(found),
                severity="error",
            ),
        )
    if not found:
        return None, ()
    return _field(BookingField.APPOINTMENT_DATE, found[0], 0.88, transcript, context), ()


def _extract_time(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    normalized = transcript.lower()
    values: list[BookingFieldValue] = []
    issues: list[BookingIssue] = []
    twelve_hour_spans: list[tuple[int, int]] = []

    for match in _TIME_12H_CANDIDATE_RE.finditer(normalized):
        twelve_hour_spans.append(match.span())
        raw = match.group(0)
        hour = int(match.group(1))
        minute_text = match.group(2)
        minute = int(minute_text or "0")
        if hour < 1 or hour > 12 or minute > 59:
            issues.append(
                BookingIssue(
                    code="invalid_time",
                    field=BookingField.APPOINTMENT_TIME,
                    message="Appointment time is invalid.",
                    value=raw,
                    severity="error",
                )
            )
            continue
        minute_suffix = f":{minute_text}" if minute_text else ""
        values.append(
            _field(
                BookingField.APPOINTMENT_TIME,
                f"{hour}{minute_suffix} {match.group(3).upper()}",
                0.95,
                transcript,
                context,
            )
        )

    for match in _TIME_24H_CANDIDATE_RE.finditer(normalized):
        if any(start <= match.start() and match.end() <= end for start, end in twelve_hour_spans):
            continue
        raw = match.group(0)
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            issues.append(
                BookingIssue(
                    code="invalid_time",
                    field=BookingField.APPOINTMENT_TIME,
                    message="Appointment time is invalid.",
                    value=raw,
                    severity="error",
                )
            )
            continue
        if not any(value.value == raw for value in values):
            values.append(_field(BookingField.APPOINTMENT_TIME, raw, 0.92, transcript, context))

    if not values:
        for word, (label, confidence) in _TIME_WORDS.items():
            if _term_matches(word, normalized):
                values.append(
                    _field(BookingField.APPOINTMENT_TIME, label, confidence, transcript, context)
                )
                break

    if not values:
        match = _AMBIGUOUS_HOUR_RE.search(normalized)
        if match:
            hour = int(match.group(1))
            if 1 <= hour <= 12:
                value = _field(
                    BookingField.APPOINTMENT_TIME,
                    str(hour),
                    0.45,
                    transcript,
                    context,
                )
                return value, (
                    BookingIssue(
                        code="ambiguous_time",
                        field=BookingField.APPOINTMENT_TIME,
                        message="Time was mentioned without AM or PM.",
                        value=str(hour),
                    ),
                )
            return None, (
                BookingIssue(
                    code="invalid_time",
                    field=BookingField.APPOINTMENT_TIME,
                    message="Appointment time is invalid.",
                    value=match.group(0),
                    severity="error",
                ),
            )

    unique_values = _dedupe([value.value for value in values])
    if len(unique_values) > 1:
        return None, (
            *tuple(issues),
            BookingIssue(
                code="conflicting_time_values",
                field=BookingField.APPOINTMENT_TIME,
                message="Multiple appointment times were mentioned.",
                value=", ".join(unique_values),
                severity="error",
            ),
        )
    return (values[0] if values else None), tuple(issues)


def _extract_doctor(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    if _ANY_DOCTOR_RE.search(transcript):
        return _field(BookingField.DOCTOR_PREFERENCE, "any doctor", 0.92, transcript, context), ()
    match = _DOCTOR_RE.search(transcript)
    if match:
        name = _clean_name(match.group(1))
        if name:
            return (
                _field(BookingField.DOCTOR_PREFERENCE, f"Dr. {name}", 0.88, transcript, context),
                (),
            )
    return None, ()


def _extract_notes(
    transcript: str,
    context: ExtractionContext,
) -> tuple[BookingFieldValue | None, tuple[BookingIssue, ...]]:
    if "notes" not in context.pending_fields and "optional_notes" not in context.pending_fields:
        return None, ()
    if _NO_NOTES_RE.search(transcript):
        return _field(BookingField.NOTES, "", 0.9, transcript, context), ()
    match = _NOTES_RE.search(transcript)
    if match:
        notes = _clean_text(match.group(1))
        if notes:
            return _field(BookingField.NOTES, notes, 0.78, transcript, context), ()
    if "doctor_preference" not in context.pending_fields and len(transcript.split()) >= 3:
        if not _contains_date_or_time(transcript) and not has_booking_intent(transcript):
            return _field(BookingField.NOTES, _clean_text(transcript), 0.62, transcript, context), ()
    return None, ()


def _field(
    field: BookingField,
    value: str,
    confidence: float,
    transcript: str,
    context: ExtractionContext,
) -> BookingFieldValue:
    return BookingFieldValue(
        field=field,
        value=_clean_text(value),
        confidence=confidence,
        source_text=transcript,
        language=context.language,
    )


def _contains_date_or_time(transcript: str) -> bool:
    normalized = transcript.lower()
    return (
        any(_term_matches(word, normalized) for word in _DATE_WORDS)
        or any(_term_matches(word, normalized) for word in _TIME_WORDS)
        or bool(_TIME_12H_CANDIDATE_RE.search(normalized))
        or bool(_TIME_24H_CANDIDATE_RE.search(normalized))
    )


def _looks_like_service_or_doctor(transcript: str) -> bool:
    normalized = transcript.lower()
    return any(
        word in normalized
        for word in ("doctor", "dr", "cleaning", "treatment", "service", "canal", "braces")
    )


def _looks_like_non_service(value: str) -> bool:
    normalized = value.lower()
    if _contains_date_or_time(normalized):
        return True
    return normalized in {"appointment", "booking", "visit", "consultation"}


def _clean_service_candidate(value: str) -> str | None:
    candidate = value
    for stopword in _SERVICE_STOPWORDS:
        parts = re.split(rf"\b{re.escape(stopword)}\b", candidate, maxsplit=1, flags=re.I)
        candidate = parts[0]
    candidate = re.sub(r"[^A-Za-z0-9 .'-]", " ", candidate)
    candidate = _clean_text(candidate)
    if len(candidate) < 3:
        return None
    return candidate


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = _PHONE_CANDIDATE_RE.sub(" ", value)
    value = _TIME_12H_CANDIDATE_RE.sub(" ", value)
    value = _TIME_24H_CANDIDATE_RE.sub(" ", value)
    for stopword in (*_DATE_OR_TIME_STOPWORDS, "phone", "number", "appointment", "booking"):
        value = re.split(rf"\b{re.escape(stopword)}\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\b(?:sir|madam|please|ji|hai|hoon)\b", " ", value, flags=re.I)
    value = re.sub(r"[^A-Za-z .'-]", " ", value)
    value = _clean_text(value)
    value = value.strip(" .'-")
    if len(value) < 2:
        return None
    return " ".join(part.capitalize() for part in value.split())


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _term_matches(term: str, text: str) -> tuple[re.Match[str], ...]:
    if term.isascii():
        return tuple(re.finditer(rf"\b{re.escape(term)}\b", text))
    return tuple(re.finditer(re.escape(term), text))


def _span_overlaps(span: tuple[int, int], existing: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(
        start < existing_end and existing_start < end
        for existing_start, existing_end in existing
    )
