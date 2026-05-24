# Phase 4A Operational Runbook

This runbook is for EasyPanel production operations. It assumes the stack has four services: `frontend`, `backend`, `worker`, and `postgres`.

## Pre-Deployment Checks

1. Confirm service environment consistency.
   - `backend` and `worker` must share `DATABASE_URL`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `SARVAM_API_KEY`, and `OPENAI_API_KEY`.
   - `frontend` must point server-side traffic at `http://backend:8000` through `API_BASE_URL`.
   - Set `DEPLOYMENT_VERSION` to the release SHA or deployment tag before rollout.
2. Confirm health surfaces.
   - Backend liveness: `GET /health`
   - Backend container readiness: `GET /readyz`
   - Full deployment readiness: `GET /ready`
   - Runtime liveness and queue state: `GET /live`
   - Operational diagnostics: `GET /internal/v1/deployment/diagnostics`
3. Confirm PostgreSQL volume persistence is attached before deployment.
4. Confirm worker `stop_grace_period` is greater than `WORKER_DRAIN_TIMEOUT_SECONDS`.

## Worker Restart

1. Check `GET /internal/v1/deployment/diagnostics` and record `active_calls`, `active_sessions`, and `queue_usage_ratio`.
2. If `active_calls > 0`, prefer a rolling restart window with low call volume.
3. Restart only the `worker` service in EasyPanel.
4. Watch logs for `worker_shutdown_started`, `worker_shutdown_completed`, `worker_started`, and `worker_ready`.
5. Re-check diagnostics.
   - `worker.status` should return to `healthy`.
   - `stale_worker_count` should remain `0` after one heartbeat interval.
   - No new `worker_shutdown_timeout`, `heartbeat_missing`, or `worker_stale` alerts should appear.

## Backend Restart

1. Check `GET /readyz` and `GET /live`.
2. Restart only the `backend` service in EasyPanel.
3. Watch logs for `backend_started`, `api_database_ready`, and `backend_ready`.
4. Confirm `GET /readyz` returns `200`.
5. Confirm the frontend recovers without changing `NEXT_PUBLIC_API_BASE_URL`.

## PostgreSQL Restart

1. Confirm `postgres` volume is healthy and backed by persistent storage.
2. Restart the `postgres` service.
3. Watch backend logs for `database_schema_initialization_retrying` during startup or `postgres_reconnected` during runtime recovery.
4. Watch worker logs for `persistence_retry_triggered`, `postgres_reconnected`, and `persistence_write_completed`.
5. Confirm `GET /ready` returns `200` after recovery.
6. Validate no duplicate booking or fulfillment records were produced. Booking fingerprints and notification idempotency keys are the authoritative dedupe controls.

## EasyPanel Stack Deployment

1. Deploy `postgres` first or verify it is already healthy.
2. Deploy `backend` and wait for `/readyz`.
3. Deploy `worker` and wait for `worker_ready` plus a fresh heartbeat.
4. Deploy `frontend` last and confirm it reaches `backend`.
5. Run:
   - `python scripts/validate_deployment_infrastructure.py`
   - `python -m pytest -q`
6. Record the deployment version and diagnostics snapshot in the release notes.

## Rollback Procedure

1. Keep the PostgreSQL volume unchanged.
2. Roll back `backend` and `worker` to the same previous image tag.
3. Roll back `frontend` only after backend and worker are healthy.
4. Confirm `/readyz`, `/ready`, and `/live`.
5. Check recent alerts for `postgres_unavailable`, `worker_stale`, `queue_events_dropped`, and `room_reconnect_failure`.

## Queue-Pressure Incident

1. Check `/live` for `queue_depth`, `queue_capacity`, and `queue_usage_ratio`.
2. If `queue_pressure_high` appears, reduce new call intake or add worker capacity.
3. If `queue_events_dropped` appears, treat persistence as degraded and validate call, booking, and notification integrity.
4. Do not restart PostgreSQL while queue pressure is high unless PostgreSQL is the root cause.

## LiveKit Reconnect Incident

1. Check worker logs for `room_disconnected`, `call_reconnect_attempt`, `room_reconnected`, and `call_recovered`.
2. If reconnects fail repeatedly, verify LiveKit project status, SIP dispatch rules, and worker registration.
3. Check `/live` for `reconnect_count` and `failed_reconnects`.
4. Escalate if active calls do not recover before timeout.

## Stale-Worker Recovery

1. Check diagnostics for `worker.status = stale` or `stale_worker_count > 0`.
2. Confirm the stale worker's calls are marked `abandoned` only when heartbeat expiry is real.
3. Restart the affected worker container.
4. Confirm a new heartbeat appears before accepting new traffic.
5. Investigate any `worker_shutdown_timeout` alerts before the next deployment.

## Real Deployment Drill Checklist

- [ ] Restart backend during an active call.
- [ ] Restart worker during an active call.
- [ ] Restart PostgreSQL during booking reservation.
- [ ] Restart PostgreSQL during notification fulfillment.
- [ ] Restart the full EasyPanel stack.
- [ ] Confirm diagnostics return to `ready`.
- [ ] Confirm no duplicate booking execution.
- [ ] Confirm no duplicate fulfillment execution.
- [ ] Confirm all required lifecycle logs include deployment version, container identity, and uptime.

