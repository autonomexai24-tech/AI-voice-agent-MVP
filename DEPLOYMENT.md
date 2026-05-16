# Deployment Foundation

Phase 3A supports three independently deployable services:

```txt
PostgreSQL
FastAPI backend
Next.js frontend
```

## Local Docker Compose

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

Expected local URLs:

```txt
Frontend: http://localhost:3000
Backend:  http://localhost:8000
Health:   http://localhost:8000/readyz
Ready:    http://localhost:8000/readyz
```

The backend initializes the PostgreSQL schema inside the FastAPI startup
lifecycle when
`INITIALIZE_DATABASE_ON_STARTUP=true`.

## EasyPanel Services

Create one PostgreSQL service and two app services.

Backend:

```txt
Dockerfile: Dockerfile.backend
Port:       8000
Health:     /readyz
Command:    default image command
```

Required backend environment:

```txt
DATABASE_URL=postgresql://postgres:<password>@<postgres-service-host>:5432/<database>
PERSISTENCE_ENABLED=true
INITIALIZE_DATABASE_ON_STARTUP=true
LIVEKIT_URL=<value>
LIVEKIT_API_KEY=<value>
LIVEKIT_API_SECRET=<value>
SARVAM_API_KEY=<value>
OPENAI_API_KEY=<value>
```

Frontend:

```txt
Build context: frontend
Dockerfile:    Dockerfile
Port:          3000
Command:       default image command
```

Required frontend environment:

```txt
API_BASE_URL=http://<backend-service-host>:8000
NEXT_PUBLIC_API_BASE_URL=https://<public-backend-domain>
FRONTEND_API_TIMEOUT_MS=8000
```

Use the internal backend service URL for `API_BASE_URL`; the Next.js server uses
that for dashboard data loading. Use the public backend URL for
`NEXT_PUBLIC_API_BASE_URL` only when browser-visible API URLs are needed.
