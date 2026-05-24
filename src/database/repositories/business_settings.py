from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from database.models.business_settings import BusinessSettingsModel

DEFAULT_SETTINGS_ID = "default"


@dataclass(frozen=True)
class BusinessSettingsUpdate:
    business_name: str | None = None
    business_type: str | None = None
    services: tuple[str, ...] | None = None
    receptionist_tone: str | None = None
    receptionist_personality: str | None = None
    faqs: tuple[dict, ...] | None = None
    default_language: str | None = None
    greeting_prompt: str | None = None
    refusal_policy: str | None = None


class BusinessSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, settings_id: str = DEFAULT_SETTINGS_ID) -> BusinessSettingsModel | None:
        return await self._session.get(BusinessSettingsModel, settings_id)

    async def upsert(
        self,
        payload: BusinessSettingsUpdate,
        *,
        settings_id: str = DEFAULT_SETTINGS_ID,
    ) -> BusinessSettingsModel:
        model = await self.get(settings_id)
        if model is None:
            model = BusinessSettingsModel(
                settings_id=settings_id,
                business_name=payload.business_name or "our clinic",
                business_type=payload.business_type or "clinic",
                services=list(payload.services or ()),
                receptionist_tone=payload.receptionist_tone,
                receptionist_personality=payload.receptionist_personality,
                faqs=list(payload.faqs or ()),
                default_language=payload.default_language or "english",
                greeting_prompt=payload.greeting_prompt,
                refusal_policy=payload.refusal_policy,
            )
            self._session.add(model)
        else:
            for field_name, value in (
                ("business_name", payload.business_name),
                ("business_type", payload.business_type),
                ("services", list(payload.services) if payload.services is not None else None),
                ("receptionist_tone", payload.receptionist_tone),
                ("receptionist_personality", payload.receptionist_personality),
                ("faqs", list(payload.faqs) if payload.faqs is not None else None),
                ("default_language", payload.default_language),
                ("greeting_prompt", payload.greeting_prompt),
                ("refusal_policy", payload.refusal_policy),
            ):
                if value is not None:
                    setattr(model, field_name, value)

        await self._session.flush()
        await self._session.refresh(model)
        return model
