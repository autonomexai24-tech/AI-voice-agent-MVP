from __future__ import annotations

from dataclasses import dataclass


FULFILLMENT_SAFE_STATUSES = frozenset({"queued", "sending", "retrying", "sent", "delivered"})
FULFILLMENT_SUCCESS_STATUSES = frozenset({"sent", "delivered"})
FULFILLMENT_FAILURE_STATUSES = frozenset({"failed", "retry_exhausted"})


@dataclass(frozen=True)
class NotificationFulfillmentState:
    notification_id: str | None
    booking_fingerprint: str | None
    idempotency_key: str
    notification_type: str
    provider: str
    status: str
    attempts: int
    fulfillment_language: str | None = None
    provider_request_id: str | None = None
    error_detail: str | None = None
    persisted: bool = False
    duplicate_prevented: bool = False

    @property
    def retry_status(self) -> str:
        if self.status == "retry_exhausted":
            return "exhausted"
        if self.status == "retrying":
            return "retrying"
        if self.status in FULFILLMENT_SUCCESS_STATUSES:
            return "complete"
        if self.status == "failed":
            return "failed"
        return "pending"
