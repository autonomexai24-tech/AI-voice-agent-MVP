from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_agent.providers.calcom import AvailableSlot

_TIME_12H_RE = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", re.I)
_TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b")

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
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class TimePreference:
    label: str
    start: clock_time
    end: clock_time
    exact: bool = False


def _extract_date(transcript: str, now: datetime) -> date | None:
    normalized = transcript.lower()
    today = now.date()
    if "day after tomorrow" in normalized:
        return today + timedelta(days=2)
    if "tomorrow" in normalized:
        return today + timedelta(days=1)
    if "today" in normalized:
        return today

    match = _ISO_DATE_RE.search(normalized)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _SLASH_DATE_RE.search(normalized)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    month_pattern = re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("
        + "|".join(_MONTHS)
        + r")(?:\s+(20\d{2}))?\b",
        re.I,
    )
    match = month_pattern.search(normalized)
    if match:
        day = int(match.group(1))
        month = _MONTHS[match.group(2).lower()]
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    reverse_month_pattern = re.compile(
        r"\b("
        + "|".join(_MONTHS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?\b",
        re.I,
    )
    match = reverse_month_pattern.search(normalized)
    if match:
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year = int(match.group(3) or today.year)
        parsed = _safe_date(year, month, day)
        if parsed is not None and parsed < today:
            parsed = _safe_date(year + 1, month, day)
        return parsed

    for weekday, index in _WEEKDAYS.items():
        if re.search(rf"\b(?:next\s+)?{weekday}\b", normalized):
            days = (index - today.weekday()) % 7
            if days == 0 or f"next {weekday}" in normalized:
                days += 7
            return today + timedelta(days=days)
    return None


def _extract_time_preference(transcript: str) -> TimePreference | None:
    normalized = transcript.lower()
    match = _TIME_12H_RE.search(normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        meridiem = match.group(3).lower()
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
        return _exact_time(hour, minute)

    match = _TIME_24H_RE.search(normalized)
    if match:
        return _exact_time(int(match.group(1)), int(match.group(2)))

    if "noon" in normalized:
        return _exact_time(12, 0)
    if "morning" in normalized:
        return TimePreference("morning", clock_time(9, 0), clock_time(12, 0))
    if "afternoon" in normalized:
        return TimePreference("afternoon", clock_time(12, 0), clock_time(17, 0))
    if "evening" in normalized:
        return TimePreference("evening", clock_time(17, 0), clock_time(20, 0))
    return None


def _find_matching_slot(
    slots: list[AvailableSlot],
    *,
    preferred_date: date,
    preference: TimePreference,
    tz: timezone,
) -> AvailableSlot | None:
    for slot in slots:
        local_start = slot.start.astimezone(tz)
        if local_start.date() != preferred_date:
            continue
        start_time = local_start.time().replace(second=0, microsecond=0)
        if preference.exact and start_time == preference.start:
            return slot
        if not preference.exact and preference.start <= start_time < preference.end:
            return slot
    return None


def _format_slot(slot: datetime, now: datetime, tz: timezone) -> str:
    local = slot.astimezone(tz)
    date_text = _format_slot_date(local.date(), now.date())
    time_text = _format_time(local.time())
    if date_text:
        return f"{date_text} at {time_text}"
    return time_text


def _timezone_for(name: str) -> timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Kolkata", "Asia/Calcutta"}:
            return timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
        return timezone.utc


def _exact_time(hour: int, minute: int) -> TimePreference | None:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    value = clock_time(hour, minute)
    return TimePreference(_format_time(value), value, value, exact=True)


def _format_slot_date(value: date, today: date) -> str:
    if value == today:
        return "today"
    if value == today + timedelta(days=1):
        return "tomorrow"
    return value.strftime("%A")


def _format_time(value: clock_time) -> str:
    hour = value.hour
    minute = value.minute
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    if minute:
        return f"{display_hour}:{minute:02d} {suffix}"
    return f"{display_hour} {suffix}"


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
