from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.dependencies.database import get_booking_repository
from api.errors import api_error
from api.schemas.bookings import BookingListResponse, BookingRead
from api.schemas.common import PageInfo
from database.repositories.bookings import BookingRepository

router = APIRouter(prefix="/internal/v1/bookings", tags=["bookings"])


@router.get("", response_model=BookingListResponse)
async def list_bookings(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    repository: BookingRepository = Depends(get_booking_repository),
) -> BookingListResponse:
    result = await repository.list(limit=limit, cursor=cursor)
    return BookingListResponse(
        items=[BookingRead.model_validate(item) for item in result.items],
        page=PageInfo(next_cursor=result.next_cursor, limit=limit),
    )


@router.get("/{booking_id}", response_model=BookingRead)
async def get_booking(
    booking_id: str,
    repository: BookingRepository = Depends(get_booking_repository),
) -> BookingRead:
    booking = await repository.get(booking_id)
    if booking is None:
        raise api_error(
            404,
            code="booking_not_found",
            message="Booking not found",
            details={"booking_id": booking_id},
        )
    return BookingRead.model_validate(booking)
