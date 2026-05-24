from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import statistics
import time
import tracemalloc
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from database.repositories.calls import CallRepository
from database.repositories.workers import WorkerHeartbeatCreate, WorkerHeartbeatRepository
from database.session import (
    DatabaseSettings,
    create_engine,
    create_session_factory,
    session_scope,
)
from voice_agent.config import WorkerResilienceConfig
from voice_agent.logging_config import get_logger, log_event
from voice_agent.runtime_metrics import DEPLOYMENT_METRICS, record_infrastructure_alert

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WorkerSnapshot:
    worker_id: str
    active_session_count: int
    uptime_seconds: int
    current_calls: tuple[str, ...]
    memory_usage_mb: float | None
    cpu_usage_percent: float | None
    status: str


class WorkerRuntimeState:
    def __init__(self, *, worker_id: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.worker_id = worker_id
        self._clock = clock
        self._started_at = clock()
        self._active_calls: set[str] = set()
        self._draining = False

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def active_session_count(self) -> int:
        return len(self._active_calls)

    def start_draining(self) -> None:
        self._draining = True

    def track_call_started(self, call_id: str) -> None:
        self._active_calls.add(call_id)

    def track_call_ended(self, call_id: str) -> None:
        self._active_calls.discard(call_id)

    def snapshot(self) -> WorkerSnapshot:
        memory_current, _memory_peak = _memory_usage_mb()
        return WorkerSnapshot(
            worker_id=self.worker_id,
            active_session_count=len(self._active_calls),
            uptime_seconds=max(0, int(self._clock() - self._started_at)),
            current_calls=tuple(sorted(self._active_calls)),
            memory_usage_mb=memory_current,
            cpu_usage_percent=None,
            status="draining" if self._draining else "running",
        )


class WorkerHeartbeatService:
    def __init__(
        self,
        *,
        settings: DatabaseSettings,
        worker_state: WorkerRuntimeState,
        interval_seconds: float = 10.0,
        stale_after_seconds: float = 60.0,
        engine_factory: Callable[[DatabaseSettings], AsyncEngine] | None = None,
    ) -> None:
        self._settings = settings
        self._worker_state = worker_state
        self._interval_seconds = interval_seconds
        self._stale_after_seconds = stale_after_seconds
        self._engine_factory = engine_factory or create_engine
        self._engine: AsyncEngine | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self._settings.is_configured or self._settings.url is None:
            log_event(
                logger,
                "worker_heartbeat_disabled",
                worker_id=self._worker_state.worker_id,
                reason="database_not_configured",
            )
            return
        if self.is_running:
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        self._engine = self._engine_factory(self._settings)
        self._task = asyncio.create_task(self._run(), name="worker-heartbeat-service")
        log_event(
            logger,
            "worker_heartbeat_started",
            worker_id=self._worker_state.worker_id,
            interval_seconds=self._interval_seconds,
            stale_after_seconds=self._stale_after_seconds,
        )

    async def stop(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.emit_once(status="stopped")
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        log_event(
            logger,
            "worker_heartbeat_stopped",
            worker_id=self._worker_state.worker_id,
        )

    async def emit_once(self, *, status: str | None = None) -> None:
        if self._engine is None:
            return
        snapshot = self._worker_state.snapshot()
        heartbeat_status = status or snapshot.status
        factory = create_session_factory(self._engine)
        async with session_scope(factory) as session:
            model = await WorkerHeartbeatRepository(session).upsert(
                WorkerHeartbeatCreate(
                    worker_id=snapshot.worker_id,
                    active_session_count=snapshot.active_session_count,
                    uptime_seconds=snapshot.uptime_seconds,
                    current_calls=snapshot.current_calls,
                    memory_usage_mb=snapshot.memory_usage_mb,
                    cpu_usage_percent=snapshot.cpu_usage_percent,
                    status=heartbeat_status,
                    started_at=utc_now() - timedelta(seconds=snapshot.uptime_seconds),
                    last_seen_at=utc_now(),
                )
            )
            stale_workers = await WorkerHeartbeatRepository(session).list_stale(
                cutoff=utc_now() - timedelta(seconds=self._stale_after_seconds),
            )
            for stale_worker in stale_workers:
                record_infrastructure_alert(
                    logger,
                    "heartbeat_missing",
                    worker_id=stale_worker.worker_id,
                    last_seen_at=stale_worker.last_seen_at.isoformat(),
                    stale_after_seconds=self._stale_after_seconds,
                )
                log_event(
                    logger,
                    "heartbeat_missed",
                    worker_id=stale_worker.worker_id,
                    last_seen_at=stale_worker.last_seen_at.isoformat(),
                    stale_after_seconds=self._stale_after_seconds,
                )
                await WorkerHeartbeatRepository(session).mark_stale(stale_worker.worker_id)
                abandoned_call_ids = await CallRepository(session).mark_abandoned_for_worker(
                    worker_id=stale_worker.worker_id,
                    abandoned_at=utc_now(),
                    reason="worker_heartbeat_missed",
                )
                record_infrastructure_alert(
                    logger,
                    "worker_stale",
                    worker_id=stale_worker.worker_id,
                    abandoned_call_count=len(abandoned_call_ids),
                )
                log_event(
                    logger,
                    "worker_marked_stale",
                    worker_id=stale_worker.worker_id,
                    abandoned_call_ids=abandoned_call_ids,
                    abandoned_call_count=len(abandoned_call_ids),
                )
                for call_id in abandoned_call_ids:
                    log_lifecycle_event(
                        "call_abandoned",
                        room_name=call_id,
                        call_id=call_id,
                        worker_id=stale_worker.worker_id,
                        reason="worker_heartbeat_missed",
                    )
        log_event(
            logger,
            "worker_heartbeat",
            worker_id=model.worker_id,
            active_session_count=model.active_session_count,
            uptime_seconds=model.uptime_seconds,
            current_calls=list(model.current_calls),
            memory_usage_mb=model.memory_usage_mb,
            cpu_usage_percent=model.cpu_usage_percent,
            status=model.status,
        )

    async def _run(self) -> None:
        while not self._closed:
            try:
                await self.emit_once()
            except Exception as exc:
                record_infrastructure_alert(
                    logger,
                    "heartbeat_missing",
                    worker_id=self._worker_state.worker_id,
                    error_type=type(exc).__name__,
                )
                log_event(
                    logger,
                    "worker_heartbeat_failed",
                    worker_id=self._worker_state.worker_id,
                    error_type=type(exc).__name__,
                )
            await asyncio.sleep(self._interval_seconds)


class MaxCallDurationGuard:
    def __init__(
        self,
        *,
        call_id: str,
        room_name: str,
        worker_id: str,
        max_duration_seconds: float,
        warning_seconds: float,
        on_timeout: Callable[[], Any],
    ) -> None:
        self._call_id = call_id
        self._room_name = room_name
        self._worker_id = worker_id
        self._max_duration_seconds = max_duration_seconds
        self._warning_seconds = min(max(warning_seconds, 0.0), max_duration_seconds)
        self._on_timeout = on_timeout
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"max-call-duration-{self._call_id}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        warn_after = max(self._max_duration_seconds - self._warning_seconds, 0.0)
        if warn_after:
            await asyncio.sleep(warn_after)
        log_event(
            logger,
            "call_timeout_warning",
            room_name=self._room_name,
            call_id=self._call_id,
            worker_id=self._worker_id,
            max_call_duration_seconds=self._max_duration_seconds,
            terminate_in_seconds=self._warning_seconds,
            timestamp=utc_now().isoformat(),
        )
        if self._warning_seconds:
            await asyncio.sleep(self._warning_seconds)
        log_lifecycle_event(
            "call_timeout_terminated",
            room_name=self._room_name,
            call_id=self._call_id,
            worker_id=self._worker_id,
            reason="max_call_duration_exceeded",
        )
        record_infrastructure_alert(
            logger,
            "call_timeout",
            worker_id=self._worker_id,
            room_name=self._room_name,
            call_id=self._call_id,
            max_call_duration_seconds=self._max_duration_seconds,
        )
        result = self._on_timeout()
        if asyncio.iscoroutine(result):
            await result


def register_room_lifecycle_handlers(
    room: Any,
    *,
    room_name: str,
    call_id: str,
    worker_id: str,
) -> None:
    on = getattr(room, "on", None)
    if on is None:
        log_event(
            logger,
            "room_lifecycle_handlers_unavailable",
            room_name=room_name,
            call_id=call_id,
            worker_id=worker_id,
        )
        return

    def bind(room_event: str, lifecycle_event: str) -> None:
        def handler(*args: Any, **kwargs: Any) -> None:
            participant_identity = _participant_identity(args, kwargs)
            reason = _disconnect_reason(args, kwargs)
            log_lifecycle_event(
                lifecycle_event,
                room_name=room_name,
                call_id=call_id,
                worker_id=worker_id,
                participant_identity=participant_identity,
                reason=reason,
            )
            if lifecycle_event == "room_disconnected":
                DEPLOYMENT_METRICS.increment("reconnect_count")
                log_lifecycle_event(
                    "call_reconnect_attempt",
                    room_name=room_name,
                    call_id=call_id,
                    worker_id=worker_id,
                    reason=reason,
                )
            if lifecycle_event == "room_reconnected":
                log_lifecycle_event(
                    "call_reconnect_success",
                    room_name=room_name,
                    call_id=call_id,
                    worker_id=worker_id,
                )
                log_lifecycle_event(
                    "call_recovered",
                    room_name=room_name,
                    call_id=call_id,
                    worker_id=worker_id,
                )

        on(room_event)(handler)

    bind("participant_connected", "participant_joined")
    bind("participant_disconnected", "participant_disconnected")
    bind("disconnected", "room_disconnected")
    bind("reconnected", "room_reconnected")
    log_event(
        logger,
        "room_lifecycle_handlers_registered",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_id,
        events=[
            "participant_connected",
            "participant_disconnected",
            "disconnected",
            "reconnected",
        ],
    )


def log_lifecycle_event(
    event: str,
    *,
    room_name: str,
    call_id: str,
    worker_id: str,
    participant_identity: str | None = None,
    reason: str | None = None,
    **fields: Any,
) -> None:
    log_event(
        logger,
        event,
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_id,
        participant_identity=participant_identity,
        reason=reason,
        timestamp=utc_now().isoformat(),
        **fields,
    )


def build_worker_state(config: WorkerResilienceConfig) -> WorkerRuntimeState:
    return WorkerRuntimeState(worker_id=config.worker_id or f"voice-worker-{os.getpid()}")


def _participant_identity(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    candidates = list(args) + list(kwargs.values())
    for candidate in candidates:
        identity = getattr(candidate, "identity", None)
        if isinstance(identity, str) and identity:
            return identity
        sid = getattr(candidate, "sid", None)
        if isinstance(sid, str) and sid:
            return sid
    return None


def _disconnect_reason(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    for key in ("reason", "disconnect_reason"):
        value = kwargs.get(key)
        if value is not None:
            return str(value)
    for candidate in args:
        reason = getattr(candidate, "reason", None)
        if reason is not None:
            return str(reason)
        if isinstance(candidate, str):
            return candidate
    return None


def _memory_usage_mb() -> tuple[float | None, float | None]:
    try:
        current, peak = tracemalloc.get_traced_memory()
    except RuntimeError:
        return None, None
    return round(current / (1024 * 1024), 3), round(peak / (1024 * 1024), 3)


def percentile_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    clean = sorted(max(0.0, float(value)) for value in values)
    return {
        "p50": round(statistics.median(clean), 3),
        "p95": round(_percentile(clean, 95), 3),
        "p99": round(_percentile(clean, 99), 3),
    }


def _percentile(sorted_values: list[float], percentile: int) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * (percentile / 100)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
