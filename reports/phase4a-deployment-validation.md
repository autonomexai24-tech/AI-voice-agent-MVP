# Phase 4A Deployment Validation

## Scope

Phase 4A hardens deployment and infrastructure behavior only. It does not rewrite LiveKit orchestration, booking runtime ownership, multilingual state, fulfillment, or prompt architecture.

## Local Validation Implemented

- Backend exposes `/health`, `/ready`, `/live`, and `/internal/v1/deployment/diagnostics`.
- Existing `/healthz` and `/readyz` remain available for container healthchecks and startup sequencing.
- Diagnostics include PostgreSQL status, LiveKit configuration status, worker heartbeat state, active calls, stale workers, active escalations, booking throughput, fulfillment throughput, retry count, queue pressure, dropped events, reconnect count, container identity, deployment version, and uptime.
- Persistence queue pressure, dropped events, retry recovery, and database unavailability now emit structured deployment alerts.
- Backend and worker lifecycle logs include deployment version, container identity, uptime, timestamps, and worker identity where applicable.
- Docker Compose declares service dependencies, healthchecks, restart policy, graceful worker stop timing, and resource limits for all four services.
- Operational runbooks are documented in `deployment/phase4a-operational-runbook.md`.
- `scripts/validate_deployment_infrastructure.py` validates topology, dependency ordering, shared environment variables, worker resilience variables, healthchecks, and resource limits.

## EasyPanel Topology Requirements

Expected service startup order:

1. `postgres`
2. `backend`
3. `worker`
4. `frontend`

Expected connectivity:

- `backend` to `postgres` through internal `DATABASE_URL`.
- `worker` to `postgres` through the same internal `DATABASE_URL`.
- `worker` to `backend` through `BACKEND_INTERNAL_URL=http://backend:8000` for operational consistency.
- `frontend` to `backend` through `API_BASE_URL=http://backend:8000`.

## Real Deployment Drill Status

Real EasyPanel drills require access to production controls and live SIP/LiveKit traffic. The codebase now contains the health surfaces, diagnostics, alerts, lifecycle logs, topology validation script, and runbooks needed to execute those drills. The following still need to be performed in EasyPanel:

- Restart backend during an active call.
- Restart worker during an active call.
- Restart PostgreSQL during booking reservation.
- Restart PostgreSQL during notification fulfillment.
- Restart the full EasyPanel stack.
- Confirm no duplicate booking or fulfillment execution after recovery.

