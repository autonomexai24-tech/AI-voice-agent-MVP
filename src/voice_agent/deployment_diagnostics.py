from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import os
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models.bookings import BookingModel
from database.models.calls import CallModel
from database.models.notifications import NotificationDeliveryModel
from database.models.workers import WorkerHeartbeatModel
from database.session import DatabaseSettings, session_scope
from voice_agent.logging_config import get_logger
from voice_agent.runtime_metrics import DEPLOYMENT_METRICS, record_infrastructure_alert

logger = get_logger(__name__)


class DeploymentDiagnosticsService:
    def __init__(
        self,
        *,
        database_settings: DatabaseSettings,
        session_factory: async_sessionmaker[AsyncSession] | None,
        env: Mapping[str, str] | None = None,
        stale_after_seconds: float | None = None,
    ) -> None:
        self._database_settings = database_settings
        self._session_factory = session_factory
        self._env = os.environ if env is None else env
        self._stale_after_seconds = stale_after_seconds or _float_env(
            self._env,
            "WORKER_STALE_AFTER_SECONDS",
            60.0,
        )

    async def collect(self) -> dict[str, Any]:
        metrics_snapshot = DEPLOYMENT_METRICS.snapshot()
        database = await self._database_status()
        worker = await self._worker_status(database["status"] == "healthy")
        throughput = await self._throughput_status(database["status"] == "healthy")
        runtime = dict(metrics_snapshot["counters"])
        runtime.update(throughput)
        runtime["worker_uptime_seconds"] = worker.get("max_uptime_seconds", 0)
        runtime["active_calls"] = throughput.get(
            "active_calls",
            runtime.get("active_calls", 0),
        )
        runtime["concurrent_calls"] = throughput.get(
            "concurrent_calls",
            runtime.get("concurrent_calls", 0),
        )
        runtime["stale_workers"] = worker.get("stale_worker_count", 0)

        dependencies = {
            "postgres": database,
            "livekit": self._livekit_status(),
            "worker": worker,
            "persistence": {
                "status": (
                    "healthy"
                    if database["status"] == "healthy"
                    else ("disabled" if database["status"] == "disabled" else "unavailable")
                ),
                "queue_depth": metrics_snapshot["gauges"].get("queue_depth", 0.0),
                "queue_capacity": metrics_snapshot["gauges"].get("queue_capacity", 0.0),
                "queue_usage_ratio": metrics_snapshot["gauges"].get("queue_usage_ratio", 0.0),
            },
        }
        status = _overall_status(dependencies)
        return {
            "status": status,
            "service": "api",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "container_identity": metrics_snapshot["container_identity"],
            "deployment_version": metrics_snapshot["deployment_version"],
            "uptime_seconds": metrics_snapshot["uptime_seconds"],
            "dependencies": dependencies,
            "runtime": runtime,
            "alerts": metrics_snapshot["recent_alerts"],
            "environment": self.environment_audit(),
        }

    def environment_audit(self) -> dict[str, Any]:
        required_shared = (
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "SARVAM_API_KEY",
            "OPENAI_API_KEY",
            "DATABASE_URL",
        )
        backend_specific = ("HOST", "PORT", "INITIALIZE_DATABASE_ON_STARTUP")
        worker_specific = (
            "WORKER_ID",
            "WORKER_HEALTH_PORT",
            "WORKER_HEARTBEAT_INTERVAL_SECONDS",
            "WORKER_STALE_AFTER_SECONDS",
            "WORKER_DRAIN_TIMEOUT_SECONDS",
        )
        frontend_specific = (
            "API_BASE_URL",
            "NEXT_PUBLIC_API_BASE_URL",
            "FRONTEND_API_TIMEOUT_MS",
        )
        present = {
            name: bool(str(self._env.get(name, "")).strip())
            for name in required_shared + backend_specific + worker_specific + frontend_specific
        }
        missing_required = [name for name in required_shared if not present[name]]
        database_url = str(self._env.get("DATABASE_URL", ""))
        livekit_url = str(self._env.get("LIVEKIT_URL", ""))
        return {
            "status": "consistent" if not missing_required else "incomplete",
            "missing_required": missing_required,
            "configured": present,
            "database_host": _host_hint(database_url),
            "livekit_scheme": livekit_url.split(":", 1)[0] if ":" in livekit_url else None,
            "worker_id": self._env.get("WORKER_ID"),
            "backend_port": self._env.get("PORT"),
            "worker_health_port": self._env.get("WORKER_HEALTH_PORT"),
        }

    async def _database_status(self) -> dict[str, Any]:
        if not self._database_settings.is_configured or self._session_factory is None:
            return {
                "status": "disabled",
                "configured": False,
                "pool_size": self._database_settings.pool_size,
                "max_overflow": self._database_settings.max_overflow,
            }
        started = datetime.now(timezone.utc)
        try:
            async with session_scope(self._session_factory) as session:
                await session.execute(text("select 1"))
            latency_ms = (
                datetime.now(timezone.utc) - started
            ).total_seconds() * 1000
            return {
                "status": "healthy",
                "configured": True,
                "latency_ms": round(latency_ms, 3),
                "pool_size": self._database_settings.pool_size,
                "max_overflow": self._database_settings.max_overflow,
                "pool_timeout_seconds": self._database_settings.pool_timeout_seconds,
            }
        except Exception as exc:
            record_infrastructure_alert(
                logger,
                "postgres_unavailable",
                severity="critical",
                error_type=type(exc).__name__,
            )
            return {
                "status": "unavailable",
                "configured": True,
                "error_type": type(exc).__name__,
                "pool_size": self._database_settings.pool_size,
                "max_overflow": self._database_settings.max_overflow,
            }

    async def _worker_status(self, database_healthy: bool) -> dict[str, Any]:
        if not database_healthy or self._session_factory is None:
            return {
                "status": "unknown",
                "workers": [],
                "active_worker_count": 0,
                "stale_worker_count": 0,
                "active_sessions": 0,
                "max_uptime_seconds": 0,
            }
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._stale_after_seconds)
        async with session_scope(self._session_factory) as session:
            result = await session.execute(select(WorkerHeartbeatModel))
            workers = list(result.scalars().all())

        worker_rows: list[dict[str, Any]] = []
        active_count = 0
        stale_count = 0
        active_sessions = 0
        max_uptime = 0
        for worker in workers:
            last_seen = _ensure_aware(worker.last_seen_at)
            is_stale = last_seen < cutoff or worker.status == "stale"
            if is_stale:
                stale_count += 1
            elif worker.status in {"running", "draining"}:
                active_count += 1
            active_sessions += int(worker.active_session_count or 0)
            max_uptime = max(max_uptime, int(worker.uptime_seconds or 0))
            worker_rows.append(
                {
                    "worker_id": worker.worker_id,
                    "status": "stale" if is_stale else worker.status,
                    "last_seen_at": last_seen.isoformat(),
                    "active_session_count": worker.active_session_count,
                    "uptime_seconds": worker.uptime_seconds,
                    "current_calls": list(worker.current_calls or []),
                    "memory_usage_mb": worker.memory_usage_mb,
                    "cpu_usage_percent": worker.cpu_usage_percent,
                }
            )

        if stale_count:
            DEPLOYMENT_METRICS.set_counter("stale_workers", stale_count)
            record_infrastructure_alert(
                logger,
                "worker_stale",
                stale_worker_count=stale_count,
            )

        status = "healthy" if active_count else ("degraded" if workers else "missing")
        return {
            "status": status,
            "workers": worker_rows,
            "active_worker_count": active_count,
            "stale_worker_count": stale_count,
            "active_sessions": active_sessions,
            "max_uptime_seconds": max_uptime,
            "stale_after_seconds": self._stale_after_seconds,
        }

    async def _throughput_status(self, database_healthy: bool) -> dict[str, int]:
        if not database_healthy or self._session_factory is None:
            return {
                "active_calls": 0,
                "concurrent_calls": 0,
                "active_escalations": 0,
                "booking_throughput": 0,
                "fulfillment_throughput": 0,
                "retry_count": 0,
            }
        async with session_scope(self._session_factory) as session:
            active_calls = await _count(
                session,
                select(func.count())
                .select_from(CallModel)
                .where(CallModel.status.in_(("active", "draining"))),
            )
            active_escalations = await _count(
                session,
                select(func.count())
                .select_from(CallModel)
                .where(
                    CallModel.escalation_triggered.is_(True),
                    CallModel.status.in_(("active", "draining")),
                ),
            )
            booking_count = await _count(
                session,
                select(func.count())
                .select_from(BookingModel)
                .where(BookingModel.confirmation_status == "confirmed"),
            )
            fulfillment_count = await _count(
                session,
                select(func.count())
                .select_from(NotificationDeliveryModel)
                .where(NotificationDeliveryModel.status.in_(("sent", "delivered"))),
            )
            retry_count = await _sum(
                session,
                select(func.coalesce(func.sum(NotificationDeliveryModel.attempts), 0)),
            )
        DEPLOYMENT_METRICS.set_counter("active_calls", active_calls)
        DEPLOYMENT_METRICS.set_counter("concurrent_calls", active_calls)
        DEPLOYMENT_METRICS.set_counter("booking_throughput", booking_count)
        DEPLOYMENT_METRICS.set_counter("fulfillment_throughput", fulfillment_count)
        DEPLOYMENT_METRICS.set_counter("retry_count", retry_count)
        return {
            "active_calls": active_calls,
            "concurrent_calls": active_calls,
            "active_escalations": active_escalations,
            "booking_throughput": booking_count,
            "fulfillment_throughput": fulfillment_count,
            "retry_count": retry_count,
        }

    def _livekit_status(self) -> dict[str, Any]:
        configured = all(
            str(self._env.get(name, "")).strip()
            for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        )
        return {
            "status": "configured" if configured else "missing_config",
            "configured": configured,
            "url_present": bool(str(self._env.get("LIVEKIT_URL", "")).strip()),
            "api_key_present": bool(str(self._env.get("LIVEKIT_API_KEY", "")).strip()),
            "api_secret_present": bool(str(self._env.get("LIVEKIT_API_SECRET", "")).strip()),
        }


async def _count(session: AsyncSession, statement: Any) -> int:
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


async def _sum(session: AsyncSession, statement: Any) -> int:
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


def _overall_status(dependencies: dict[str, dict[str, Any]]) -> str:
    postgres_status = dependencies["postgres"]["status"]
    livekit_status = dependencies["livekit"]["status"]
    worker_status = dependencies["worker"]["status"]
    persistence_status = dependencies["persistence"]["status"]
    if postgres_status == "healthy" and livekit_status == "configured" and worker_status == "healthy":
        return "ready"
    if postgres_status in {"unavailable"} or persistence_status == "unavailable":
        return "down"
    return "degraded"


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _host_hint(url: str) -> str | None:
    if "@" in url:
        tail = url.split("@", 1)[1]
    else:
        tail = url.split("://", 1)[-1]
    if not tail or tail == url:
        return None
    return tail.split("/", 1)[0].split(":", 1)[0]
