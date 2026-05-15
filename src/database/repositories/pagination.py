from __future__ import annotations

import base64
import json
from datetime import datetime

MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    return max(1, min(limit, MAX_PAGE_LIMIT))


def encode_cursor(*, timestamp: datetime, identifier: str) -> str:
    payload = {"timestamp": timestamp.isoformat(), "id": identifier}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(payload["timestamp"]), str(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
