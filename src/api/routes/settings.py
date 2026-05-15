from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies.database import get_business_settings_repository
from api.errors import api_error
from api.schemas.settings import BusinessSettingsPatch, BusinessSettingsRead
from database.repositories.business_settings import (
    BusinessSettingsRepository,
    BusinessSettingsUpdate,
)
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/v1/settings", tags=["settings"])


@router.get("/business", response_model=BusinessSettingsRead)
async def get_business_settings(
    repository: BusinessSettingsRepository = Depends(get_business_settings_repository),
) -> BusinessSettingsRead:
    settings = await repository.get()
    if settings is None:
        raise api_error(
            404,
            code="business_settings_not_found",
            message="Business settings have not been configured",
        )
    log_event(
        logger,
        "settings_loaded",
        settings_id=settings.settings_id,
        services_count=len(settings.services),
        default_language=settings.default_language,
    )
    return BusinessSettingsRead.model_validate(settings)


@router.patch("/business", response_model=BusinessSettingsRead)
async def update_business_settings(
    patch: BusinessSettingsPatch,
    repository: BusinessSettingsRepository = Depends(get_business_settings_repository),
) -> BusinessSettingsRead:
    settings = await repository.upsert(
        BusinessSettingsUpdate(
            business_name=patch.business_name,
            business_type=patch.business_type,
            services=tuple(patch.services) if patch.services is not None else None,
            receptionist_tone=patch.receptionist_tone,
            default_language=patch.default_language,
            greeting_prompt=patch.greeting_prompt,
            refusal_policy=patch.refusal_policy,
        )
    )
    log_event(
        logger,
        "settings_updated",
        settings_id=settings.settings_id,
        services_count=len(settings.services),
        default_language=settings.default_language,
    )
    return BusinessSettingsRead.model_validate(settings)
