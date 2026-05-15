from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.dependencies.database import get_call_repository
from api.errors import api_error
from api.schemas.calls import CallListResponse, CallRead
from api.schemas.common import PageInfo
from database.repositories.calls import CallRepository

router = APIRouter(prefix="/internal/v1/calls", tags=["calls"])


@router.get("", response_model=CallListResponse)
async def list_calls(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    repository: CallRepository = Depends(get_call_repository),
) -> CallListResponse:
    result = await repository.list(limit=limit, cursor=cursor)
    return CallListResponse(
        items=[CallRead.model_validate(item) for item in result.items],
        page=PageInfo(next_cursor=result.next_cursor, limit=limit),
    )


@router.get("/{call_id}", response_model=CallRead)
async def get_call(
    call_id: str,
    repository: CallRepository = Depends(get_call_repository),
) -> CallRead:
    call = await repository.get(call_id)
    if call is None:
        raise api_error(
            404,
            code="call_not_found",
            message="Call not found",
            details={"call_id": call_id},
        )
    return CallRead.model_validate(call)
