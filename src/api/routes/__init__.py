from __future__ import annotations

from api.routes.bookings import router as bookings_router
from api.routes.calls import router as calls_router
from api.routes.settings import router as settings_router
from api.routes.transcripts import router as transcripts_router

__all__ = [
    "bookings_router",
    "calls_router",
    "settings_router",
    "transcripts_router",
]
