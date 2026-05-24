from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any

from voice_agent.logging_config import log_event

PROCESS_STARTED_AT = time.monotonic()

INFRASTRUCTURE_ALERT_EVENTS = {
    "worker_stale",
    "heartbeat_missing",
    "queue_pressure_high",
    "queue_events_dropped",
    "postgres_reconnect",
    "postgres_unavailable",
    "call_timeout",
    "room_reconnect_failure",
    "worker_shutdown_timeout",
}


@dataclass(frozen=True)
class DeploymentAlert:
    event: str
    severity: str
    timestamp: str
    fields: dict[str, Any]


class DeploymentMetricsRegistry:
    def __init__(self, *, recent_alert_limit: int = 50) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, int] = {
            "active_calls": 0,
            "concurrent_calls": 0,
            "reconnect_count": 0,
            "failed_reconnects": 0,
            "stale_workers": 0,
            "booking_throughput": 0,
            "fulfillment_throughput": 0,
            "retry_count": 0,
            "persistence_queue_pressure": 0,
            "queue_events_dropped": 0,
        }
        self._gauges: dict[str, float] = {
            "queue_depth": 0.0,
            "queue_capacity": 0.0,
            "queue_usage_ratio": 0.0,
            "reconnect_recovery_latency_ms": 0.0,
        }
        self._alerts: deque[DeploymentAlert] = deque(maxlen=recent_alert_limit)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_counter(self, name: str, value: int) -> None:
        with self._lock:
            self._counters[name] = max(0, int(value))

    def set_gauge(self, name: str, value: float | int | None) -> None:
        with self._lock:
            self._gauges[name] = 0.0 if value is None else float(value)

    def record_alert(
        self,
        event: str,
        *,
        severity: str = "warning",
        **fields: Any,
    ) -> None:
        with self._lock:
            self._alerts.append(
                DeploymentAlert(
                    event=event,
                    severity=severity,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    fields=dict(fields),
                )
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "recent_alerts": [
                    {
                        "event": alert.event,
                        "severity": alert.severity,
                        "timestamp": alert.timestamp,
                        **alert.fields,
                    }
                    for alert in self._alerts
                ],
                "uptime_seconds": process_uptime_seconds(),
                "container_identity": container_identity(),
                "deployment_version": deployment_version(),
            }

    def reset(self) -> None:
        with self._lock:
            for key in list(self._counters):
                self._counters[key] = 0
            for key in list(self._gauges):
                self._gauges[key] = 0.0
            self._alerts.clear()


DEPLOYMENT_METRICS = DeploymentMetricsRegistry()


def container_identity() -> str:
    return (
        os.getenv("CONTAINER_ID")
        or os.getenv("HOSTNAME")
        or os.getenv("COMPUTERNAME")
        or "unknown"
    )


def deployment_version() -> str:
    return (
        os.getenv("DEPLOYMENT_VERSION")
        or os.getenv("GIT_SHA")
        or os.getenv("APP_VERSION")
        or "unknown"
    )


def process_uptime_seconds() -> int:
    return max(0, int(time.monotonic() - PROCESS_STARTED_AT))


def deployment_log_fields(*, worker_id: str | None = None) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "worker_id": worker_id,
        "container_identity": container_identity(),
        "deployment_version": deployment_version(),
        "uptime_seconds": process_uptime_seconds(),
    }


def log_deployment_event(logger, event: str, *, worker_id: str | None = None, **fields: Any) -> None:
    log_event(
        logger,
        event,
        **deployment_log_fields(worker_id=worker_id),
        **fields,
    )


def record_infrastructure_alert(
    logger,
    event: str,
    *,
    severity: str = "warning",
    worker_id: str | None = None,
    **fields: Any,
) -> None:
    alert_fields = dict(fields)
    if worker_id is not None:
        alert_fields["worker_id"] = worker_id
    DEPLOYMENT_METRICS.record_alert(event, severity=severity, **alert_fields)
    log_deployment_event(
        logger,
        event,
        worker_id=worker_id,
        severity=severity,
        **fields,
    )
