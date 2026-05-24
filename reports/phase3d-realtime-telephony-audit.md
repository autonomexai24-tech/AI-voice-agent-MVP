# PHASE 3D — REALTIME CALL FLOW INTEGRATION AUDIT

**Date:** 2026-05-24
**Auditor role:** Senior Realtime Telephony Infrastructure Architect
**Scope:** Full production telephony resilience analysis

---

## PART 1 — CURRENT REALTIME ARCHITECTURE

### Complete Call Flow (Traced from Code)

```
Phone Call (PSTN/SIP)
  ↓
LiveKit SIP Trunk (cloud-managed)
  ↓
LiveKit creates room: e.g. "917676808950_mTyE6avLmZmo"
  ↓
LiveKit dispatches job to registered worker ("voice-calling-agent")
  ↓
worker.py → _request_handler(req) → auto-accept (req.accept())
  ↓
worker.py → entrypoint(ctx: JobContext)
  ├── load_config() from .env (infra keys, models, URLs)
  ├── RuntimeContextLoader.load() → PostgreSQL → RuntimeContextSnapshot
  │   └── fallback to .env BusinessConfig on DB failure
  ├── ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
  ├── BusinessPromptOrchestrator.load() → build_instructions()
  ├── RuntimePersistenceService.start() → async queue writer
  ├── SimpleClinicAgent creation (STT/LLM/TTS plugins)
  └── AgentSession(turn_detection="stt").start(agent, room)
        ↓
    LiveKit AgentSession owns:
      ├── STT streaming (Sarvam STT plugin, VAD, endpointing)
      ├── Turn detection (STT-native, min_endpointing_delay=0.07s)
      ├── LLM generation (OpenAI GPT-4o-mini)
      ├── TTS streaming (Sarvam TTS plugin)
      ├── Interruption handling (framework-managed)
      └── Playback control (framework-managed)
        ↓
    SimpleClinicAgent callbacks:
      ├── on_enter() → greeting, generate_reply()
      └── on_user_turn_completed() → per-turn orchestration:
            ├── Language routing (SessionLanguageRouter)
            ├── Memory assembly (CallSessionMemory + CallMemoryEngine)
            ├── Persistence enqueue (transcripts)
            ├── RuntimeConversationOrchestrator.handle_turn()
            │   ├── Intent classification (regex-based deterministic)
            │   ├── Booking workflow (ConversationalBookingFlow)
            │   ├── FAQ retrieval (FAQRetrievalEngine)
            │   ├── Escalation evaluation (HumanTakeoverRuntime)
            │   └── State transition (ConversationRuntimeState FSM)
            └── Prompt recomposition (RealtimePromptManager)
                → update_instructions() → next LLM generation
        ↓
Call teardown:
  ├── session.start() returns (framework handles disconnect)
  ├── finally block:
  │   ├── enqueue_call_ended
  │   ├── persistence_service.stop(drain_timeout)
  │   └── notification_orchestrator.aclose()
  └── Worker process continues for next job
```

### Runtime Authority Map

| Authority | Owner | Notes |
|---|---|---|
| **WebSocket to LiveKit** | LiveKit Agents framework | Worker has zero control |
| **Room lifecycle** | LiveKit Cloud | Room created by SIP dispatch |
| **Participant events** | LiveKit Agents framework | `ctx.connect()` auto-subscribes |
| **Audio transport** | LiveKit Agents framework | Managed by AgentSession |
| **STT streaming** | Sarvam STT plugin | VAD + endpointing internal |
| **Turn detection** | AgentSession (turn_detection="stt") | Framework-managed |
| **Interruption handling** | AgentSession | Framework-managed (not custom code) |
| **LLM generation** | AgentSession + OpenAI plugin | Framework-managed |
| **TTS streaming** | Sarvam TTS plugin | Framework-managed |
| **Prompt orchestration** | SimpleClinicAgent.on_user_turn_completed | App-level |
| **Business state** | ConversationOrchestrator + CallSessionMemory | App-level, in-memory |
| **Persistence** | RuntimePersistenceService (async queue) | App-level, fire-and-forget |
| **Reconnect handling** | LiveKit Agents framework | **NO app-level handling** |
| **Cleanup** | Worker `finally` block | Persistence drain only |

### Critical Observation: Dual Architecture

The codebase contains **two distinct interruption/conversation systems**:

1. **Active system:** LiveKit `AgentSession` with Sarvam plugins — manages all realtime audio, VAD, interruptions, turn-taking natively
2. **Legacy system:** `RealtimeInterruptionManager` in `interruption.py` — a full custom 4-queue interruption pipeline with generation tracking, PCM RMS detection, playback cancellation

The active `agent_v2.py` does **not** use `RealtimeInterruptionManager`. The `AgentSession` framework manages interruptions internally. The `RealtimeInterruptionManager` appears to be dead code from the pre-plugin architecture. The `ConversationOrchestrator` in `conversation/orchestrator.py` (used by the interruption manager) is a **different class** from `RuntimeConversationOrchestrator` in `orchestration/conversation_orchestrator.py` (used by agent_v2).

---

## PART 2 — REALTIME INFRASTRUCTURE RISKS

### CRITICAL RISKS (Production-Breaking)

#### RISK-01: Zero Reconnect Handling — SEVERITY: CRITICAL

**Location:** `worker.py` entrypoint, `agent_v2.py`

There is **no reconnect handling anywhere in the application code**. The entire call lifecycle depends on:

1. LiveKit Agents framework reconnect behavior (opaque)
2. Sarvam STT websocket reconnect behavior (opaque)
3. Sarvam TTS HTTP resilience (opaque)

**What happens on network glitch:**
- LiveKit room WebSocket drops → framework may reconnect, but app has no hook
- All in-memory state (`CallSessionMemory`, `ConversationOrchestrator`, `HumanTakeoverRuntime`) is **per-process** and **not persisted**
- If the worker process restarts, ALL call state is lost — booking progress, language state, escalation state, conversation context
- No `on_reconnect` callback, no `on_participant_reconnected` handler, no reconnect guard

**Impact:** A single transient network issue can silently break a live call's business state while audio continues.

#### RISK-02: No Worker Crash Recovery — SEVERITY: CRITICAL

**Location:** `worker.py` entrypoint

The `entrypoint()` function runs as a single async coroutine per job. If any unhandled exception occurs after `session.start()`, the `finally` block runs persistence drain. But:

- `session.start()` is the **last await** — the function awaits indefinitely here
- If the process crashes (OOM, segfault in native LiveKit SDK code), the `finally` block **never runs**
- Call end event is never persisted
- Persistence queue may have undrained items
- No external heartbeat or watchdog monitors whether the worker is still alive mid-call

**Impact:** Ghost calls — calls that appear active in the system but the worker is dead.

#### RISK-03: All Business State is In-Memory Only — SEVERITY: CRITICAL

**Location:** `session_memory.py`, `call_memory_engine.py`, `human_takeover/`

Every piece of business state is in a Python dataclass in worker process memory:
- `CallSessionMemory` — booking fields, language, escalation, turns
- `CallMemoryEngine` — caller identity, booking continuity, language continuity, corrections, unresolved questions
- `HumanTakeoverRuntime._states` — dict of `_TakeoverSessionState`
- `EscalationQueue._items` — dict of `EscalationQueueItem`
- `ConversationOrchestrator._current_state` — FSM state
- `SessionLanguageRouter._active_language` — language state

None of this survives:
- Worker restart
- Process crash
- Container restart (EasyPanel deploy)
- LiveKit session reconnect to a different worker

**Impact:** A deployment restart during active calls kills all in-flight booking progress, language state, and escalation state with zero recovery.

---

### HIGH RISKS (Degraded Production Experience)

#### RISK-04: Human Takeover is Simulated, Not Wired — SEVERITY: HIGH

**Location:** `human_takeover_runtime.py`

The `HumanTakeoverRuntime` is a fully implemented **in-memory simulation**:
- `EscalationQueue` with operators, assignments, timeouts, priorities
- State machine (ACTIVE_AI → PENDING_ESCALATION → WAITING_FOR_HUMAN → HUMAN_ACTIVE → AI_RESUMED)
- Handoff payload construction
- Operator disconnect handling

But it is **not connected to any real operator system**:
- No LiveKit room participant joining as operator
- No external notification (no Slack, no SMS, no push notification to operator app)
- No real-time audio routing change — the SIP caller hears the AI the entire time
- The "operator" is assigned from a hardcoded tuple: `("supervisor-1", "Clinic Coordinator 1")`
- `HUMAN_ACTIVE` state is set immediately by queue assignment, but no actual human ever connects

The escalation detection works (regex patterns, frustration detection, booking failure loops), but the **execution is a no-op** from the caller's perspective. The AI just says "I'll transfer you" and continues being the AI.

**Impact:** Escalation appears functional in logs and tests but provides zero actual human takeover in production.

#### RISK-05: Sarvam STT WebSocket Fragility — SEVERITY: HIGH

**Location:** Managed by `livekit-agents[sarvam]` plugin, not directly visible

Sarvam STT uses a WebSocket connection (`wss://api.sarvam.ai/speech-to-text/ws`). The plugin manages this internally. Risks:

- Sarvam WebSocket disconnect mid-utterance → transcript lost, turn never completes
- Sarvam API rate limiting → STT stops, caller gets silence
- VAD sensitivity mismatch → premature endpointing or missed speech
- No fallback STT provider configured
- No health check on Sarvam connection quality

The `stt_language="unknown"` parameter means Sarvam auto-detects language. If auto-detection fails or picks wrong language, the entire STT quality degrades silently.

**Impact:** A single Sarvam outage takes down ALL calls simultaneously with no fallback.

#### RISK-06: Persistence Queue Silently Drops Events — SEVERITY: HIGH

**Location:** `persistence.py` lines 364-376, `runtime_persistence.py` lines 150-171

The persistence system uses a bounded `asyncio.Queue(maxsize=500)`:
- `_enqueue()` uses `put_nowait()` — if full, event is **silently dropped** with a log
- `safe_enqueue()` wraps all calls in try/except — failures are logged but **never retried**
- `_write_with_retry()` does 3 attempts, then **drops the event permanently**
- No dead-letter queue, no WAL, no replay mechanism

On a long call with many turns, or during DB latency spikes, persistence events can accumulate faster than they drain. The queue has no backpressure mechanism — the call continues normally while persistence falls behind.

**Impact:** Transcripts, booking confirmations, and call metadata can be silently lost during production load.

#### RISK-07: No Graceful Shutdown During Active Calls — SEVERITY: HIGH

**Location:** `worker.py`, `docker-compose.yml`

When a container restart happens (EasyPanel deploy, health check failure):
- Docker sends SIGTERM
- `cli.run_app()` from LiveKit Agents receives the signal
- Unknown behavior: does the framework wait for active sessions to finish?
- `persistence_service.stop(drain_timeout_seconds=2.0)` — only 2 seconds to drain
- If the DB is slow, pending persistence events are lost
- Active call is terminated abruptly — caller hears disconnect

No `STOPSIGNAL` override in Dockerfile. No pre-stop hook. No drain period configuration.

**Impact:** Every deployment kills all active calls immediately.

---

### MEDIUM RISKS (Operational Degradation)

#### RISK-08: Unbounded Memory Growth on Long Calls

**Location:** `session_memory.py`, `call_memory_engine.py`

Bounded structures:
- `recent_turns`: capped at 16 (good)
- `_unresolved`: capped at 8, with aging (good)
- `_measurements` in profiler: capped at 256 (good)

Unbounded structures:
- `booking.field_confidence`: dict, grows with corrections
- `booking.field_sources`: dict, grows with corrections
- `booking.retry_counts`: dict, grows with retries
- `_interrupted_generations`: set in `RealtimeInterruptionManager` (legacy, but if used, grows forever)
- `_states` dict in `HumanTakeoverRuntime`: one per session, never cleaned
- `_items` dict in `EscalationQueue`: grows per escalation, only status changes

For typical call durations (2-10 minutes), these are negligible. For pathological cases (30+ minute calls with repeated corrections), memory could grow but unlikely to be dangerous.

**Impact:** Low risk for normal calls; watch for edge cases in monitoring.

#### RISK-09: Language Switching Without TTS Plugin Reconfiguration

**Location:** `agent_v2.py` lines 185-193, `language.py`

When the `SessionLanguageRouter` detects a language switch:
- The `SessionLanguageSnapshot` updates with new `sarvam_language_code`
- This is injected into the **prompt instructions** via `RealtimePromptManager`
- But the `sarvam.TTS` plugin was initialized with a **fixed** `target_language_code="en-IN"` at agent creation time

The TTS plugin language code does not change at runtime. The prompt tells GPT to respond in Hindi, but TTS still synthesizes with `en-IN` settings. This may work if Sarvam TTS is multilingual with `en-IN`, but it's an unvalidated assumption.

**Impact:** Multilingual switching may produce incorrect pronunciation or garbled audio.

#### RISK-10: Prompt Recomposition Latency Risk

**Location:** `agent_v2.py` on_user_turn_completed, `optimization/runtime_latency_optimizer.py`

The `on_user_turn_completed` callback does ALL orchestration synchronously before the next turn:
1. Language routing (async, Sarvam detection)
2. Memory assembly (sync)
3. Persistence enqueue (sync)
4. Conversation orchestration (async, includes booking API calls)
5. Prompt recomposition (sync)
6. `update_instructions()` (async)

This entire chain runs between the user finishing speaking and the AI starting to respond. Target is 2500ms total response time.

Booking orchestration may call Cal.com API (timeout: 8s, 1 retry). If the booking API is slow, the caller waits in silence for up to 16 seconds.

**Impact:** Cal.com latency directly translates to caller-perceived silence.

#### RISK-11: No Call Duration Limits

**Location:** `worker.py`, `agent_v2.py`

There is no maximum call duration enforced. The `session.start()` awaits indefinitely. A stuck call (caller leaves phone off-hook, network keeps connection alive) will:
- Hold the worker slot indefinitely
- Grow memory slowly
- Keep a LiveKit room open
- Consume Sarvam/OpenAI API credits

**Impact:** Resource leak from zombie calls.

---

## PART 3 — DEPLOYMENT RISKS

### EasyPanel / Docker Deployment Risks

#### DEPLOY-01: No Worker Container in docker-compose.yml

**Location:** `docker-compose.yml`

The `docker-compose.yml` defines:
- `postgres` — database
- `backend` — FastAPI API server
- `frontend` — Next.js

There is **no worker service** defined. The LiveKit Agents worker (`python -m voice_agent.worker start`) must be deployed separately. This means:
- Worker deployment is undocumented
- Worker restart behavior is unknown in EasyPanel
- No health check defined for the worker
- No restart policy for the worker
- Worker and backend may be running in the same container (unclear)

#### DEPLOY-02: Backend Restart Kills Nothing (But Worker Restart Kills Everything)

The `backend` container (FastAPI) is stateless — it reads from PostgreSQL. Restarting it is safe.

But the **worker** process holds ALL call state in memory. A worker restart:
- Terminates all active LiveKit sessions
- Loses all in-flight booking progress
- Drops all persistence queue items
- Disconnects all callers

#### DEPLOY-03: PostgreSQL Connection Pool Not Validated for Reconnect

**Location:** `database/session.py`

The SQLAlchemy engine uses `pool_pre_ping=True`, which validates connections before use. This is the correct setting for PostgreSQL reconnect after restart.

However, there is a subtle risk: `build_runtime_context_loader()` creates its **own** engine (separate from the persistence service engine, separate from the API engine). That's 3 SQLAlchemy engines per worker, each with `pool_size=5 + max_overflow=10`. For 5 concurrent calls, that's:
- Context loader: up to 15 connections
- Persistence service: up to 15 connections
- Total per worker: up to 30 connections

PostgreSQL default `max_connections=100`. If multiple workers are running, connection exhaustion is possible.

#### DEPLOY-04: No Container Orchestration for Zero-Downtime Deploy

With EasyPanel:
- Deploy = restart container
- No rolling update (single container per service)
- No connection draining
- Active calls are terminated on every deploy

---

## PART 4 — TARGET PRODUCTION TELEPHONY ARCHITECTURE

### Reconnect Authority

**Current:** None — total framework delegation with no app-level awareness.

**Target:**
1. Register `@room.on("reconnected")` handler in worker to log reconnect events
2. Register `@room.on("disconnected")` handler to trigger graceful cleanup
3. Register `@room.on("participant_disconnected")` to detect caller hangup vs network drop
4. Add `on_close` / lifecycle hook in AgentSession (if framework supports) for session cleanup
5. Consider periodic checkpoint of business state to PostgreSQL (every N turns) to enable recovery

### WebSocket Recovery

**Current:** Entirely delegated to LiveKit Agents framework and Sarvam plugin.

**Target:**
1. Add health monitoring: log Sarvam STT/TTS latency per turn (already partially done via profiler)
2. Add circuit breaker: if 3 consecutive turns have no STT result, log `stt_health_degraded`
3. Add timeout guard: if `on_user_turn_completed` is not called within 60s of last activity, trigger heartbeat check
4. Consider Sarvam connection health ping if plugin exposes it

### Participant Lifecycle Ownership

**Current:** Framework-managed, no app-level hooks.

**Target:**
1. Track participant join/leave events at app level
2. Distinguish between:
   - Caller hangup (normal disconnect) → graceful teardown
   - Network drop (timeout disconnect) → potential reconnect window
   - Worker crash → external monitoring required
3. Add `call_id`, `participant_identity` to all lifecycle logs for tracing

### Escalation Continuity

**Current:** Simulated in-memory, no real operator connection.

**Target (Phase 4 scope):**
1. Decide operator channel: LiveKit room participant, separate WebRTC, or phone bridge
2. Wire `HumanTakeoverRuntime` to actual operator notification (Slack webhook, SMS, push)
3. Implement audio routing: mute AI, unmute operator track
4. Implement AI resume: on operator disconnect, restore AI with preserved context

### Teardown Safety

**Current:** `finally` block drains persistence with 2s timeout.

**Target:**
1. Register SIGTERM handler in worker for graceful shutdown
2. On SIGTERM: stop accepting new jobs, wait for active session to complete (up to 30s)
3. Increase drain timeout for graceful shutdown path
4. Add call-end persistence as highest-priority write (skip queue, write directly)
5. Add external heartbeat: worker reports alive status to backend API every 10s
6. Add dead-call detector: backend marks calls as `abandoned` if no heartbeat for 60s

---

## PART 5 — REAL-WORLD VALIDATION PLAN

### LOW RISK — Local Runtime Probes

These can be run immediately with no production impact.

| # | Probe | How to Validate | Pass Criteria |
|---|---|---|---|
| L1 | Worker startup and job acceptance | `python -m voice_agent.worker dev` with test room | Logs show `worker_job_received`, `runtime_snapshot_created`, `worker_session_started` |
| L2 | Persistence queue drain on clean shutdown | Start worker, send SIGTERM, check logs | `persistence_service.stop()` logs drain complete, no `db_write_failed` |
| L3 | DB failure fallback | Start worker with wrong DATABASE_URL, make call | `runtime_context_loaded` with `context_source=env_fallback`, call still works |
| L4 | Persistence queue full behavior | Set `DATABASE_QUEUE_MAX_ITEMS=2`, make call with many turns | `db_write_failed` with `reason=persistence_queue_full` logged, call unaffected |
| L5 | Long prompt recomposition | Populate all booking fields, many FAQ matches | `runtime_latency_profile` shows `prompt_composition` < 60ms target |
| L6 | Language detection accuracy | Call with Hindi, English, Hinglish transcripts | `language_detected` logs show correct `detected_language` and `confidence` |
| L7 | Memory engine compression | Simulate 30+ turns | `runtime_memory_compressed` fires, injection stays under 760 chars |
| L8 | Escalation trigger accuracy | Send "I want to speak to a human" | `escalation_trigger` with `should_escalate=true`, `reason=caller_escalation_request` |

### MEDIUM RISK — Controlled Staging Calls

Requires LiveKit SIP trunk connected to a test phone number.

| # | Test | How to Validate | Pass Criteria |
|---|---|---|---|
| M1 | Full SIP call lifecycle | Call test number, have 3-turn conversation | Greeting plays, STT works, GPT responds, TTS plays, call ends cleanly |
| M2 | Booking flow end-to-end | Complete all 7 booking fields in a call | All fields captured, Cal.com booking created, SMS notification sent |
| M3 | Caller hangup mid-booking | Hang up during date collection | `call_ended` persisted with correct duration, no crash, partial booking in DB |
| M4 | Sarvam latency measurement | Make 10 calls, measure STT/TTS latency | All turns < 2500ms total response, STT < 600ms, TTS < 500ms |
| M5 | Concurrent calls | 3 simultaneous calls to same worker | All 3 get independent business context, no state cross-contamination |
| M6 | Language switching live | Start in English, switch to Hindi mid-call | Language routing updates, prompt reflects Hindi, TTS quality acceptable |
| M7 | Worker restart during call | Start a call, restart worker container | Caller hears disconnect, call_ended may not persist (validates the risk) |
| M8 | DB restart during call | Start a call, restart PostgreSQL container | Next persistence write fails, retries, recovers after DB is back |
| M9 | Escalation trigger live | Say "I want to speak to a manager" | AI responds with handoff message, logs show escalation flow, caller stays with AI |

### HIGH RISK — Production Telephony Validation

Only after staging validation passes. Requires real phone number, real callers, real stakes.

| # | Test | How to Validate | Pass Criteria |
|---|---|---|---|
| H1 | First real call | Single test call from a real phone | Full lifecycle works, greeting sounds natural, <3s first response |
| H2 | Noise resilience | Call from noisy environment | STT handles noise gracefully, no infinite retries or silence |
| H3 | Long call stability | 10+ minute call with interruptions | No memory degradation, no latency increase, clean teardown |
| H4 | Deploy during active calls | Deploy while 2 calls are active | Calls terminate (expected), new calls work immediately after |
| H5 | Network instability | Call from area with poor signal | Reconnect behavior documented, partial audio handled |
| H6 | Peak load | 5 simultaneous real calls | All calls responsive, no worker crash, DB connections stable |
| H7 | Observability validation | Review logs from H1-H6 | Every call traceable end-to-end by room_name, all latencies measurable |

---

## APPENDIX A — PRIORITY IMPLEMENTATION ROADMAP

If moving to implementation, the priority order should be:

### P0 — Before First Production Call
1. **Add room event handlers** — `on("disconnected")`, `on("participant_disconnected")` for observability
2. **Add worker health check endpoint** — HTTP endpoint reporting active session count
3. **Add SIGTERM graceful shutdown** — Stop accepting jobs, wait for active session
4. **Add max call duration guard** — Timeout after configurable limit (default: 30 minutes)
5. **Validate TTS language code propagation** — Confirm Sarvam TTS handles multilingual without plugin reconfig

### P1 — Before Production Load
6. **Add periodic state checkpoint** — Write booking progress to PostgreSQL every 5 turns
7. **Add dead-letter queue for persistence** — Failed writes go to local file for retry
8. **Add connection pool monitoring** — Log pool exhaustion warnings
9. **Wire escalation notification** — Even just a Slack webhook for operator alert
10. **Add worker to docker-compose.yml** — Complete deployment definition

### P2 — Production Hardening
11. **Add reconnect state recovery** — Load last checkpoint from DB on reconnect
12. **Add Sarvam health circuit breaker** — Fallback behavior on STT/TTS failure
13. **Add concurrent call limit** — Worker refuses jobs beyond capacity
14. **Add call recording** — Required for production telephony compliance
15. **Clean up dead code** — Remove `interruption.py`, `livekit_audio.py` (legacy pipeline)

---

## APPENDIX B — KEY FILE REFERENCE

| File | Role | Risk Surface |
|---|---|---|
| `worker.py` | Entrypoint, lifecycle | Crash recovery, shutdown |
| `agent_v2.py` | Per-turn orchestration | Latency, state management |
| `session_memory.py` | All booking/call state | In-memory only, no persistence |
| `call_memory_engine.py` | Structured memory | In-memory only, growth bounds |
| `human_takeover_runtime.py` | Escalation simulation | Not wired to real operators |
| `persistence.py` | Async DB writes | Queue overflow, silent drops |
| `runtime_context.py` | Per-call DB context load | Engine proliferation |
| `language.py` | Multilingual routing | TTS not reconfigured |
| `conversation_orchestrator.py` | Intent routing, FSM | Latency from booking API |
| `interruption.py` | Legacy interruption system | Dead code (not used by agent_v2) |
| `config.py` | All configuration | No runtime config reload |
| `docker-compose.yml` | Deployment definition | Missing worker service |
