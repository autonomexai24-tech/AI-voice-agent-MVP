from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

from voice_agent.config import CalComConfig
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class CalComAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        operation: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code


@dataclass(frozen=True)
class AvailableSlot:
    start: datetime


@dataclass(frozen=True)
class CalComBookingRequest:
    start: datetime
    attendee_name: str
    attendee_phone: str
    attendee_email: str


@dataclass(frozen=True)
class CalComBookingConfirmation:
    uid: str | None
    start: datetime
    status: str | None


class CalComClient:
    def __init__(
        self,
        config: CalComConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds)
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_available_slots(
        self,
        *,
        start: date,
        end: date,
        request_id: str | None = None,
    ) -> list[AvailableSlot]:
        if not self._config.is_configured:
            raise CalComAPIError("Cal.com is not configured", operation="availability")

        params: dict[str, str | int] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeZone": self._config.time_zone,
            "duration": self._config.duration_minutes,
            "format": "range",
        }
        params.update(_event_type_params(self._config))

        started_at = time.perf_counter()
        log_event(
            logger,
            "calcom_availability_check_started",
            request_id=request_id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            duration_minutes=self._config.duration_minutes,
            event_type_id=self._config.event_type_id,
            event_type_slug=self._config.event_type_slug,
        )
        try:
            response = await self._request_with_retry(
                "GET",
                "slots",
                operation="availability",
                api_version=self._config.slots_api_version,
                params=params,
                request_id=request_id,
            )
        except Exception:
            log_event(
                logger,
                "calcom_availability_check_failed",
                request_id=request_id,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            raise
        data = response.get("data")
        if not isinstance(data, dict):
            raise CalComAPIError("Cal.com slots response was invalid", operation="availability")

        slots = _parse_slots(data)
        latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        log_event(
            logger,
            "calcom_availability_check_completed",
            request_id=request_id,
            slots_count=len(slots),
            latency_ms=latency_ms,
        )
        return slots

    async def create_booking(
        self,
        booking: CalComBookingRequest,
        *,
        request_id: str | None = None,
    ) -> CalComBookingConfirmation:
        if not self._config.is_configured:
            raise CalComAPIError("Cal.com is not configured", operation="booking")

        payload: dict[str, Any] = {
            "start": _to_utc_iso(booking.start),
            "attendee": {
                "name": booking.attendee_name,
                "timeZone": self._config.time_zone,
                "phoneNumber": booking.attendee_phone,
                "email": booking.attendee_email,
                "language": "en",
            },
            "lengthInMinutes": self._config.duration_minutes,
            "metadata": {"source": "voice_agent_phone_call"},
        }
        payload.update(_event_type_body(self._config))

        started_at = time.perf_counter()
        log_event(
            logger,
            "calcom_booking_attempt_started",
            request_id=request_id,
            booking_start=_to_utc_iso(booking.start),
            duration_minutes=self._config.duration_minutes,
            event_type_id=self._config.event_type_id,
            event_type_slug=self._config.event_type_slug,
            attendee_name_present=bool(booking.attendee_name),
            attendee_phone_present=bool(booking.attendee_phone),
        )
        try:
            response = await self._request_once(
                "POST",
                "bookings",
                operation="booking",
                api_version=self._config.bookings_api_version,
                json=payload,
            )

            data = response.get("data")
            if not isinstance(data, dict):
                raise CalComAPIError("Cal.com booking response was invalid", operation="booking")

            confirmation = CalComBookingConfirmation(
                uid=_string_or_none(data.get("uid")),
                start=_parse_datetime(_string_or_none(data.get("start"))) or booking.start,
                status=_string_or_none(data.get("status")),
            )
        except Exception:
            log_event(
                logger,
                "calcom_booking_failed",
                request_id=request_id,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            raise
        log_event(
            logger,
            "calcom_booking_confirmed",
            request_id=request_id,
            booking_uid=confirmation.uid,
            status=confirmation.status,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
        )
        return confirmation

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        api_version: str,
        request_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        attempts = max(1, self._config.retry_attempts + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(
                    method,
                    path,
                    operation=operation,
                    api_version=api_version,
                    **kwargs,
                )
            except CalComAPIError as exc:
                last_error = exc
                retryable = exc.status_code is None or exc.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if not retryable or attempt >= attempts:
                    raise
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise CalComAPIError(
                        "Cal.com request failed",
                        operation=operation,
                    ) from exc

            log_event(
                logger,
                "calcom_api_retry_scheduled",
                request_id=request_id,
                operation=operation,
                attempt=attempt,
                max_attempts=attempts,
            )
            await asyncio.sleep(min(0.25 * attempt, 1.0))

        raise CalComAPIError("Cal.com request failed", operation=operation) from last_error

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        api_version: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = await self._client.request(
                method,
                url,
                headers=_headers(self._config, api_version),
                **kwargs,
            )
        except httpx.RequestError as exc:
            raise CalComAPIError("Cal.com request failed", operation=operation) from exc

        if response.status_code >= 400:
            raise CalComAPIError(
                "Cal.com request returned an error",
                operation=operation,
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalComAPIError("Cal.com response was not JSON", operation=operation) from exc
        if not isinstance(payload, dict):
            raise CalComAPIError("Cal.com response was invalid", operation=operation)
        if payload.get("status") == "error":
            raise CalComAPIError("Cal.com returned error status", operation=operation)
        return payload


def _headers(config: CalComConfig, api_version: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key or ''}",
        "cal-api-version": api_version,
    }
    return headers


def _event_type_params(config: CalComConfig) -> dict[str, str | int]:
    if config.event_type_id is not None:
        return {"eventTypeId": config.event_type_id}

    params: dict[str, str | int] = {}
    if config.event_type_slug:
        params["eventTypeSlug"] = config.event_type_slug
    if config.username:
        params["username"] = config.username
    if config.team_slug:
        params["teamSlug"] = config.team_slug
    if config.organization_slug:
        params["organizationSlug"] = config.organization_slug
    return params


def _event_type_body(config: CalComConfig) -> dict[str, str | int]:
    return dict(_event_type_params(config))


def _parse_slots(data: dict[str, Any]) -> list[AvailableSlot]:
    slots: list[AvailableSlot] = []
    for value in data.values():
        if not isinstance(value, list):
            continue
        for item in value:
            start_value: str | None = None
            if isinstance(item, str):
                start_value = item
            elif isinstance(item, dict):
                start_value = _string_or_none(item.get("start"))
            start = _parse_datetime(start_value)
            if start is not None:
                slots.append(AvailableSlot(start=start))
    return sorted(slots, key=lambda slot: slot.start)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
