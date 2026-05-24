from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.notifications import (
    NotificationDeliveryModel,
    new_notification_id,
)

SUCCESSFUL_NOTIFICATION_STATUSES = frozenset({"sent", "delivered"})
ACTIVE_NOTIFICATION_STATUSES = frozenset({"queued", "sending", "retrying"})
TERMINAL_NOTIFICATION_STATUSES = frozenset(
    {"sent", "delivered", "failed", "retry_exhausted"}
)


@dataclass(frozen=True)
class NotificationDeliveryCreate:
    booking_fingerprint: str | None
    notification_type: str
    idempotency_key: str
    provider: str
    fulfillment_language: str | None = None


class NotificationDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_queued(
        self,
        payload: NotificationDeliveryCreate,
    ) -> NotificationDeliveryModel:
        existing = await self.get_by_idempotency_key(payload.idempotency_key)
        if existing is not None:
            return existing

        model = NotificationDeliveryModel(
            notification_id=new_notification_id(),
            booking_fingerprint=payload.booking_fingerprint,
            notification_type=payload.notification_type,
            idempotency_key=payload.idempotency_key,
            provider=payload.provider,
            status="queued",
            attempts=0,
            fulfillment_language=payload.fulfillment_language,
            created_at=_utc_now(),
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
            return model
        except IntegrityError:
            await self._session.rollback()
            existing = await self.get_by_idempotency_key(payload.idempotency_key)
            if existing is None:
                raise
            return existing

    async def get(self, notification_id: str) -> NotificationDeliveryModel | None:
        return await self._session.get(NotificationDeliveryModel, notification_id)

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> NotificationDeliveryModel | None:
        result = await self._session.execute(
            select(NotificationDeliveryModel)
            .where(NotificationDeliveryModel.idempotency_key == idempotency_key)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_successful_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> NotificationDeliveryModel | None:
        result = await self._session.execute(
            select(NotificationDeliveryModel)
            .where(NotificationDeliveryModel.idempotency_key == idempotency_key)
            .where(NotificationDeliveryModel.status.in_(SUCCESSFUL_NOTIFICATION_STATUSES))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_booking_fingerprint(
        self,
        booking_fingerprint: str,
    ) -> list[NotificationDeliveryModel]:
        result = await self._session.execute(
            select(NotificationDeliveryModel)
            .where(NotificationDeliveryModel.booking_fingerprint == booking_fingerprint)
            .order_by(NotificationDeliveryModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_sending(
        self,
        notification_id: str,
        *,
        attempted_at: datetime | None = None,
    ) -> NotificationDeliveryModel | None:
        model = await self.get(notification_id)
        if model is None:
            return None
        model.status = "sending"
        model.attempts += 1
        model.last_attempt_at = attempted_at or _utc_now()
        model.error_detail = None
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def mark_sent(
        self,
        notification_id: str,
        *,
        provider_request_id: str | None,
    ) -> NotificationDeliveryModel | None:
        model = await self.get(notification_id)
        if model is None:
            return None
        model.status = "sent"
        model.provider_request_id = provider_request_id
        model.error_detail = None
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def mark_retrying(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> NotificationDeliveryModel | None:
        return await self._mark_failed_state(
            notification_id,
            status="retrying",
            error_detail=error_detail,
            terminal=False,
        )

    async def mark_failed(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> NotificationDeliveryModel | None:
        return await self._mark_failed_state(
            notification_id,
            status="failed",
            error_detail=error_detail,
            terminal=True,
        )

    async def mark_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> NotificationDeliveryModel | None:
        return await self._mark_failed_state(
            notification_id,
            status="retry_exhausted",
            error_detail=error_detail,
            terminal=True,
        )

    async def _mark_failed_state(
        self,
        notification_id: str,
        *,
        status: str,
        error_detail: str,
        terminal: bool,
    ) -> NotificationDeliveryModel | None:
        model = await self.get(notification_id)
        if model is None:
            return None
        model.status = status
        model.error_detail = error_detail[:2000]
        if terminal:
            model.failed_at = _utc_now()
        await self._session.flush()
        await self._session.refresh(model)
        return model


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
