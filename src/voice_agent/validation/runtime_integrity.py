from __future__ import annotations

import re

from voice_agent.fulfillment import FULFILLMENT_SAFE_STATUSES, NotificationFulfillmentState

_CONFIRMATION_STATUSES = {"accepted", "confirmed", "confirmed_pending_payment"}


class RuntimeIntegrityValidator:
    """Single validation authority for booking, fulfillment, and safe responses."""

    def validate_booking(self, result: object) -> bool:
        return (
            bool(getattr(result, "booking_success", False))
            and bool(getattr(result, "calcom_booking_uid", None))
            and self.external_status_is_success(getattr(result, "external_status", None))
            and getattr(result, "booking_time", None) is not None
            and getattr(result, "validation_status", None) == "valid"
            and getattr(result, "persistence_status", None) == "persisted"
            and getattr(result, "booking_fingerprint", None) is not None
        )

    def validate_fulfillment(
        self,
        *,
        booking_result: object,
        notification_state: NotificationFulfillmentState | None,
        notification_required: bool,
    ) -> bool:
        if not self.validate_booking(booking_result):
            return False
        if not notification_required:
            return True
        if notification_state is None:
            return False
        return notification_state.persisted and notification_state.status in FULFILLMENT_SAFE_STATUSES

    def validate_runtime_consistency(
        self,
        *,
        call_id: str | None,
        language: str | None,
        booking_stage: str | None,
    ) -> bool:
        return bool(call_id) and bool(language) and bool(booking_stage)

    def safe_response(
        self,
        response_text: str,
        *,
        result: object,
        language: str,
    ) -> str:
        if self.validate_booking(result):
            return response_text
        if self.contains_confirmation_claim(response_text):
            return self.localized_failure(language)
        return response_text

    def external_status_is_success(self, status: str | None) -> bool:
        if status is None:
            return True
        return status.strip().lower() in _CONFIRMATION_STATUSES

    def contains_confirmation_claim(self, text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:appointment|booking)\b.{0,40}\bconfirmed\b|\bconfirmed\b.{0,40}\b(?:appointment|booking)\b",
                text,
                re.I,
            )
        )

    def localized_failure(self, language: str) -> str:
        if language in {"hindi", "hinglish", "mixed"}:
            return (
                "Sorry, appointment abhi confirm nahi ho paya. "
                "Clinic team slot verify karke follow up karegi."
            )
        return (
            "Sorry sir, I'm unable to confirm the appointment right now. "
            "Please try again in a few minutes."
        )
