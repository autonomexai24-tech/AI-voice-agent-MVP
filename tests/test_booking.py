from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import httpx

from voice_agent.config import CalComConfig
from voice_agent.providers.calcom import (
    CalComBookingRequest,
    CalComClient,
)


def test_calcom_client_checks_slots_and_creates_booking_with_v2_headers() -> None:
    asyncio.run(_run_calcom_client_http_test())


async def _run_calcom_client_http_test() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/v2/slots"
            assert request.headers["authorization"] == "Bearer cal_test_key"
            assert request.headers["cal-api-version"] == "2024-09-04"
            assert request.url.params["eventTypeId"] == "123"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "2026-05-14": [
                            {"start": "2026-05-14T14:00:00.000+00:00"}
                        ]
                    },
                },
            )
        assert request.method == "POST"
        assert request.url.path == "/v2/bookings"
        assert request.headers["authorization"] == "Bearer cal_test_key"
        assert request.headers["cal-api-version"] == "2026-02-25"
        payload = json.loads(request.content)
        assert payload["eventTypeId"] == 123
        assert payload["start"] == "2026-05-14T14:00:00Z"
        assert payload["attendee"]["name"] == "Ravi Kumar"
        assert payload["attendee"]["phoneNumber"] == "+919876543210"
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "booking_uid_123",
                    "status": "accepted",
                    "start": "2026-05-14T14:00:00Z",
                },
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.cal.com",
    )
    client = CalComClient(_calcom_config(), http_client=http_client)

    slots = await client.get_available_slots(
        start=date(2026, 5, 14),
        end=date(2026, 5, 16),
        request_id="req-1",
    )
    confirmation = await client.create_booking(
        CalComBookingRequest(
            start=slots[0].start,
            attendee_name="Ravi Kumar",
            attendee_phone="+919876543210",
            attendee_email="reception@example.com",
        ),
        request_id="req-2",
    )
    await http_client.aclose()

    assert len(requests) == 2
    assert slots[0].start == datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc)
    assert confirmation.uid == "booking_uid_123"
    assert confirmation.status == "accepted"


def _calcom_config(api_key: str | None = "cal_test_key") -> CalComConfig:
    return CalComConfig(
        api_key=api_key,
        base_url="https://api.cal.com/v2",
        slots_api_version="2024-09-04",
        bookings_api_version="2026-02-25",
        event_type_id=123,
        event_type_slug=None,
        username=None,
        team_slug=None,
        organization_slug=None,
        time_zone="UTC",
        duration_minutes=30,
        timeout_seconds=3.0,
        retry_attempts=1,
        default_attendee_email="reception@example.com",
    )
