from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from voice_agent.config import Fast2SMSConfig
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class Fast2SMSAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class Fast2SMSSendResult:
    request_id: str | None
    messages: tuple[str, ...]


class Fast2SMSClient:
    def __init__(
        self,
        config: Fast2SMSConfig,
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

    async def send_sms(
        self,
        *,
        phone_number: str,
        message: str,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Fast2SMSSendResult:
        if not self._config.is_configured:
            raise Fast2SMSAPIError("Fast2SMS is not configured", retryable=False)

        number = _fast2sms_number(phone_number)
        body = _clean_message(message)
        started_at = time.perf_counter()
        attempts = max(1, self._config.retry_attempts + 1)
        last_error: Exception | None = None

        log_event(
            logger,
            "fast2sms_sms_request_started",
            request_id=request_id,
            notification_key=idempotency_key,
            route=self._config.route,
            recipient_last4=number[-4:],
            message_chars=len(body),
        )

        for attempt in range(1, attempts + 1):
            try:
                result = await self._request_once(number=number, message=body)
            except Fast2SMSAPIError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                    log_event(
                        logger,
                        "fast2sms_sms_request_failed",
                        request_id=request_id,
                        notification_key=idempotency_key,
                        attempt=attempt,
                        max_attempts=attempts,
                        status_code=exc.status_code,
                        error_type=type(exc).__name__,
                        latency_ms=latency_ms,
                    )
                    raise
            except httpx.TimeoutException as exc:
                last_error = exc
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                log_event(
                    logger,
                    "fast2sms_sms_request_failed",
                    request_id=request_id,
                    notification_key=idempotency_key,
                    attempt=attempt,
                    max_attempts=attempts,
                    error_type=type(exc).__name__,
                    latency_ms=latency_ms,
                )
                raise Fast2SMSAPIError(
                    "Fast2SMS request timed out",
                    retryable=False,
                ) from exc
            except httpx.ConnectError as exc:
                last_error = exc
                if attempt >= attempts:
                    latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                    log_event(
                        logger,
                        "fast2sms_sms_request_failed",
                        request_id=request_id,
                        notification_key=idempotency_key,
                        attempt=attempt,
                        max_attempts=attempts,
                        error_type=type(exc).__name__,
                        latency_ms=latency_ms,
                    )
                    raise Fast2SMSAPIError(
                        "Fast2SMS connection failed",
                        retryable=True,
                    ) from exc
            except httpx.RequestError as exc:
                last_error = exc
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                log_event(
                    logger,
                    "fast2sms_sms_request_failed",
                    request_id=request_id,
                    notification_key=idempotency_key,
                    attempt=attempt,
                    max_attempts=attempts,
                    error_type=type(exc).__name__,
                    latency_ms=latency_ms,
                )
                raise Fast2SMSAPIError(
                    "Fast2SMS request failed",
                    retryable=False,
                ) from exc
            else:
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                log_event(
                    logger,
                    "fast2sms_sms_request_succeeded",
                    request_id=request_id,
                    notification_key=idempotency_key,
                    fast2sms_request_id=result.request_id,
                    attempt=attempt,
                    max_attempts=attempts,
                    latency_ms=latency_ms,
                )
                return result

            log_event(
                logger,
                "fast2sms_sms_retry_scheduled",
                request_id=request_id,
                notification_key=idempotency_key,
                attempt=attempt,
                max_attempts=attempts,
            )
            await asyncio.sleep(min(0.25 * attempt, 1.0))

        raise Fast2SMSAPIError("Fast2SMS request failed", retryable=False) from last_error

    async def _request_once(self, *, number: str, message: str) -> Fast2SMSSendResult:
        response = await self._client.post(
            self._config.base_url,
            headers={
                "authorization": self._config.api_key or "",
                "cache-control": "no-cache",
            },
            data={
                "message": message,
                "language": self._config.language,
                "route": self._config.route,
                "numbers": number,
            },
        )

        if response.status_code >= 400:
            raise Fast2SMSAPIError(
                "Fast2SMS request returned an error",
                status_code=response.status_code,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise Fast2SMSAPIError("Fast2SMS response was not JSON") from exc
        if not isinstance(payload, dict):
            raise Fast2SMSAPIError("Fast2SMS response was invalid")

        if payload.get("return") is not True:
            raise Fast2SMSAPIError(_provider_message(payload), retryable=False)

        return Fast2SMSSendResult(
            request_id=_string_or_none(payload.get("request_id")),
            messages=tuple(_message_values(payload.get("message"))),
        )


def _fast2sms_number(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[-10:]
    if len(digits) != 10:
        raise Fast2SMSAPIError("Fast2SMS requires a 10 digit Indian mobile number")
    return digits


def _clean_message(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise Fast2SMSAPIError("SMS message must not be empty")
    return text


def _provider_message(payload: dict[str, Any]) -> str:
    messages = tuple(_message_values(payload.get("message")))
    if messages:
        return "; ".join(messages)
    return "Fast2SMS returned an unsuccessful response"


def _message_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
