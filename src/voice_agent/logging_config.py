from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

SECRET_MARKERS = ("key", "secret", "token", "password", "credential")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_redact(fields))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info(event, extra={"fields": fields})


def log_error(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.exception(event, extra={"fields": fields})


def _redact(fields: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in fields.items():
        if any(marker in key.lower() for marker in SECRET_MARKERS):
            redacted[key] = "[redacted]"
        else:
            redacted[key] = value
    return redacted
