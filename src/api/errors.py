from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def api_error(
    status_code: int,
    *,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    request_id: str | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
    )


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": str(exc.detail),
                "details": {},
                "request_id": None,
            }
        },
    )
