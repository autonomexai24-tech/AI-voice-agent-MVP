# EXECUTION.md — Phase 3A

---

# Current Phase

Phase 3A — Dockerization + EasyPanel Deployment Foundation

---

# Objective

Prepare the AI Voice Receptionist MVP for reliable deployment on EasyPanel.

This phase focuses ONLY on:

- Docker-friendly backend
- Docker-friendly frontend
- PostgreSQL container support
- environment separation
- production startup scripts
- container networking
- EasyPanel compatibility
- deployment safety

This phase does NOT include:

- Kubernetes
- CI/CD pipelines
- auth systems
- billing
- SaaS features
- Redis
- background workers
- scaling clusters
- websocket dashboards
- analytics systems
- infrastructure rewrites
- transport rewrites

The goal is simple:

```txt
Push to GitHub
Import into EasyPanel
Deploy frontend
Deploy backend
Deploy PostgreSQL
System works
```

---

# Success Criteria

- Backend image builds and starts with `uvicorn` on `0.0.0.0:8000`
- Frontend image builds and starts with Next.js standalone server on `0.0.0.0:3000`
- PostgreSQL can run as a container with persistent storage
- Backend can connect to PostgreSQL over container networking
- Backend can initialize database schema safely at startup
- Frontend can reach backend through an internal service URL
- Health checks exist for deployment readiness
- Secrets remain environment-driven and are not baked into images

---

# Validation Requirements

- Run backend tests that cover API health and database configuration behavior
- Run frontend tests or build checks
- Run Docker Compose config validation when Docker is available
- Do not claim deployment completion unless build or validation evidence exists

---

# Stop Conditions

- Stop after Dockerization and EasyPanel foundation are in place
- Do not add Redis, workers, auth, billing, CI/CD, or Kubernetes
- Do not rewrite realtime transport or conversational orchestration
- Do not expand frontend scope beyond deployment compatibility
