from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx

from voice_agent.config import Fast2SMSConfig
from voice_agent.providers.fast2sms import Fast2SMSClient


def test_fast2sms_client_sends_quick_sms_form_request() -> None:
    asyncio.run(_run_fast2sms_request_test())


async def _run_fast2sms_request_test() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = parse_qs(request.content.decode())

        assert request.method == "POST"
        assert request.url == "https://www.fast2sms.com/dev/bulkV2"
        assert request.headers["authorization"] == "sms_test_key"
        assert payload["message"] == [
            "Hello Ravi, your appointment at Smile Dental Clinic is confirmed."
        ]
        assert payload["language"] == ["english"]
        assert payload["route"] == ["q"]
        assert payload["numbers"] == ["9876543210"]
        return httpx.Response(
            200,
            json={
                "return": True,
                "request_id": "sms_req_123",
                "message": ["Message sent successfully"],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Fast2SMSClient(_fast2sms_config(), http_client=http_client)

    result = await client.send_sms(
        phone_number="+919876543210",
        message="Hello Ravi, your appointment at Smile Dental Clinic is confirmed.",
        request_id="req-1",
        idempotency_key="booking:test",
    )
    await http_client.aclose()

    assert len(requests) == 1
    assert result.request_id == "sms_req_123"
    assert result.messages == ("Message sent successfully",)


def _fast2sms_config() -> Fast2SMSConfig:
    return Fast2SMSConfig(
        api_key="sms_test_key",
        base_url="https://www.fast2sms.com/dev/bulkV2",
        route="q",
        language="english",
        timeout_seconds=3.0,
        retry_attempts=1,
        queue_max_items=10,
        drain_timeout_seconds=1.0,
    )
