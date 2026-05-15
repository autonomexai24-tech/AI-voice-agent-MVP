# AI Voice Receptionist Platform — AGENTS.md

---

# 1. Purpose

This document defines:

* operational architecture rules
* execution rules
* engineering constraints
* conversational quality standards
* realtime runtime safety rules
* development philosophy
* AI execution boundaries

This repository is NOT a traditional CRUD application.

This project is:

```txt
Realtime Conversational Infrastructure
```

optimized for:

* realtime voice interactions
* multilingual AI conversations
* interruption handling
* low-latency orchestration
* configurable conversational behavior
* rapid conversational iteration

---

# 2. Core Engineering Philosophy

## 2.1 Realtime First

This project is fundamentally:

* streaming-first
* event-driven
* realtime-oriented

The architecture must prioritize:

* conversational continuity
* realtime responsiveness
* low perceived latency
* interruption recovery
* conversational realism

Avoid treating the system like:

* a CRUD dashboard
* a REST-only backend
* a simple chatbot

The conversational runtime is the core product.

---

## 2.2 Fast Iteration Velocity

The architecture must optimize for:

* safe iteration
* rapid improvements
* modular behavior
* configurable prompts
* configurable workflows
* provider replacement
* isolated execution phases

Future improvements should NOT require large rewrites.

---

## 2.3 Config-Driven Behavior

Business behavior MUST NOT be hardcoded.

Avoid:

* hardcoded receptionist flows
* hardcoded business prompts
* hardcoded clinic rules
* hardcoded workflows
* hardcoded escalation logic

Prefer:

* modular prompts
* database-driven settings
* configurable workflows
* editable agent behavior
* runtime configuration

The system should evolve into:

```txt
Configurable Conversational Agent Infrastructure
```

---

## 2.4 Conversation Quality Over Feature Quantity

Prioritize:

* reliable conversation quality
* interruption handling
* human-like pacing
* low latency
* appointment completion

Do NOT prioritize:

* excessive dashboards
* premature enterprise features
* unnecessary analytics
* complex SaaS infrastructure

Conversation quality is the primary KPI.

---

# 3. Development Strategy

## 3.1 MVP-First Development

Current priority:

```txt
Reliable AI appointment booking over realtime phone calls.
```

The MVP should remain:

* small
* focused
* stable
* testable
* iterative

Avoid:

* giant execution phases
* massive feature expansion
* unnecessary abstractions

---

## 3.2 Local-First Development

Primary development mode:

```txt
Native local development first.
```

Goals:

* fast iteration
* easier debugging
* lower operational complexity
* rapid conversational tuning

Dockerization comes later.

---

## 3.3 Small Execution Phases

Every execution phase MUST:

* solve one isolated problem
* have clear boundaries
* avoid scope expansion
* include validation steps
* include explicit stop conditions

Avoid:

* giant autonomous execution
* multi-system rewrites
* large mixed-scope tasks

---

# 4. Architecture Rules

## 4.1 Provider Abstraction

All providers must remain replaceable.

Examples:

* Sarvam
* OpenAI
* LiveKit
* VoBiz
* Cal.com

Avoid tight vendor coupling.

Provider-specific logic should remain isolated.

---

## 4.2 Realtime Runtime Separation

Separate:

* realtime voice runtime
* backend APIs
* frontend dashboard
* persistence layer

Recommended architecture:

```txt
Realtime Voice Runtime
+
FastAPI Backend
+
Next.js Frontend
+
PostgreSQL
```

Do NOT tightly couple frontend logic into realtime runtime.

---

## 4.3 State Isolation

Conversation state should remain isolated per call.

Avoid:

* global mutable state
* shared runtime memory
* uncontrolled concurrency

Conversation sessions must remain independently isolated.

---

## 4.4 Event-Driven Design

Prefer:

* events
* queues
* streaming flows
* async orchestration

Avoid:

* blocking orchestration
* synchronous audio flows
* polling-heavy architecture

---

# 5. Prompt Architecture Rules

## 5.1 Modular Prompt System

Prompts MUST remain modular.

Avoid:

* giant monolithic prompts
* hardcoded system prompts
* mixed business logic

Use layers:

* identity
* tone
* business rules
* booking rules
* multilingual rules
* refusal rules
* escalation rules
* interruption behavior

---

## 5.2 Runtime Prompt Editing

Prompt changes should:

* be editable from dashboard
* apply to new calls only
* avoid runtime instability

Avoid requiring runtime restarts for prompt updates.

---

## 5.3 Prompt Safety

The AI MUST:

* remain business-focused
* avoid unrelated topics
* refuse general knowledge conversations
* avoid hallucinated medical advice
* avoid unsafe recommendations

The MVP AI acts strictly as:

```txt
AI Receptionist
```

until future role expansion.

---

# 6. Conversation Design Rules

## 6.1 Human-Like Timing

Conversation timing matters more than verbosity.

Prioritize:

* fast acknowledgment
* natural pauses
* interruption responsiveness
* concise speech
* smooth turn-taking

Avoid:

* robotic pacing
* long paragraphs
* delayed acknowledgments
* unnatural silence

---

## 6.2 Interruption Handling

The system must support:

* barge-in interruption
* playback cancellation
* conversational recovery
* speech overlap handling

Caller speech should always take priority.

If the caller interrupts:

* AI should stop speaking quickly
* AI should recover naturally
* AI should avoid repeating entire responses

---

## 6.3 Controlled Conversation States

The runtime should evolve toward:

```txt
Controlled Conversation State Machine
```

Core states:

* greeting
* listening
* discovery
* booking
* confirmation
* escalation
* speaking
* interrupted
* closing
* ended

Avoid uncontrolled freeform orchestration.

---

## 6.4 Single Primary Workflow

The MVP should prioritize ONE core workflow:

```txt
Reliable appointment booking.
```

Avoid adding:

* complex multi-step automations
* excessive branching workflows
* broad assistant capabilities

Focus on reliability first.

---

# 7. Multilingual Rules

## 7.1 India-First Language Optimization

Primary target languages:

* Hindi
* Kannada
* Tamil
* Telugu
* Malayalam
* Marathi
* Punjabi
* Bengali
* Hinglish

The system should support:

* code-mixed conversations
* automatic language switching
* telecom-quality audio
* noisy environments

---

## 7.2 Language Adaptation

The AI should:

* detect caller language automatically
* switch naturally to caller language
* prioritize caller comfort

Example:

* default business language = Kannada
* caller speaks Hindi
* AI switches naturally to Hindi

Avoid forcing a configured language.

---

# 8. Booking Workflow Rules

## 8.1 Booking Reliability First

The booking workflow is the primary MVP KPI.

The AI must reliably collect:

* customer name
* phone number
* appointment date
* appointment time
* service type
* doctor/staff
* notes

The AI should:

* confirm details clearly
* avoid ambiguity
* validate booking information
* confirm appointment successfully

---

## 8.2 Booking Scope

Initial constraints:

* one booking per call
* single calendar only
* automatic booking creation

Avoid:

* complex scheduling systems
* multi-calendar orchestration
* advanced routing

---

## 8.3 Human Escalation

Human transfer is allowed ONLY for:

* booking disputes
* customer frustration
* unsupported requests
* operational escalation

Example:

```txt
"Please hold while I transfer your call."
```

Full call-center routing is deferred.

---

# 9. Frontend Rules

## 9.1 Frontend Role

The frontend is:

* a control panel
* operational visibility layer
* prompt management layer
* CRM interface

The frontend is NOT the core product moat.

The realtime conversational engine remains the core moat.

---

## 9.2 MVP Frontend Scope

Initial pages:

* Dashboard
* CRM + Calendar
* Agent Settings
* Call Logs

Avoid:

* large admin systems
* enterprise permissions
* premature multi-tenant dashboards

---

## 9.3 Frontend Stack

Frontend stack:

```txt
Next.js + TypeScript
```

Backend APIs:

```txt
FastAPI
```

Database:

```txt
PostgreSQL
```

---

# 10. Persistence Rules

## 10.1 Database Strategy

Database:

```txt
PostgreSQL
```

Initial persisted entities:

* contacts
* calls
* transcripts
* recordings metadata
* appointments
* prompt settings
* business configuration

Avoid premature complex schemas.

---

## 10.2 Recording Storage

Initial recording storage:

```txt
Local filesystem
```

Future migration:

* S3
* Cloudflare R2
* object storage

Keep storage abstraction clean.

---

# 11. Logging & Observability Rules

## 11.1 Structured Logging

All major events should emit structured logs.

Examples:

* SIP lifecycle
* room lifecycle
* transcript events
* TTS events
* playback events
* booking events
* interruption events

Avoid unstructured print debugging.

---

## 11.2 Observability First

Realtime systems require strong visibility.

Every critical pipeline should expose:

* state transitions
* failures
* provider errors
* latency events
* booking outcomes

Debuggability is critical.

---

# 12. Performance Rules

## 12.1 Low Latency First

Target:

```txt
Perceived response latency under 1 second.
```

Optimize for:

* fast STT turnaround
* fast AI response start
* fast TTS playback
* interruption responsiveness

---

## 12.2 Perceived Latency Over Benchmark Latency

Human conversational perception matters more than raw benchmarks.

Prioritize:

* acknowledgment speed
* natural pacing
* streaming responsiveness

Avoid optimizing meaningless synthetic metrics.

---

# 13. Safety Rules

## 13.1 Medical Safety

The MVP acts as:

```txt
AI Receptionist
```

NOT:

* doctor
* medical advisor
* diagnostic system

Avoid:

* medical diagnosis
* unsafe recommendations
* fabricated medical claims

Escalate uncertain medical conversations.

---

## 13.2 Hallucination Prevention

The AI should:

* avoid fabricated business information
* avoid fake booking confirmations
* avoid unsupported promises
* avoid guessing unavailable data

When uncertain:

* ask clarification
* escalate politely
* refuse safely

---

# 14. Deployment Rules

## 14.1 MVP Deployment

Development:

```txt
Local-first
```

Production direction:

```txt
Docker + EasyPanel
```

Avoid premature cloud complexity.

---

## 14.2 Infrastructure Simplicity

Prioritize:

* operational simplicity
* reproducibility
* debugging ease
* fast iteration

Avoid:

* Kubernetes
* distributed orchestration
* over-engineered DevOps

until truly required.

---

# 15. Future Expansion Rules

Future systems may include:

* multi-tenant SaaS
* authentication
* role-based access
* advanced analytics
* outbound calling
* sales agents
* support agents
* advanced CRM
* AI summaries
* campaign orchestration

These systems are intentionally deferred.

The MVP should remain focused.

---

# 16. Execution Rules

## 16.1 PLAN.md Stability

PLAN.md is:

* architecture memory
* product blueprint
* long-term roadmap

Avoid rewriting PLAN.md during execution.

---

## 16.2 AGENTS.md Stability

AGENTS.md defines:

* execution philosophy
* architecture boundaries
* engineering rules
* operational constraints

Avoid changing AGENTS.md frequently.

---

## 16.3 EXECUTION.md Isolation

Execution happens ONLY through:

```txt
EXECUTION.md
```

Each EXECUTION.md should contain ONLY:

* current phase
* current scope
* success criteria
* validation requirements
* stop conditions

Avoid execution scope expansion.

---

## 16.4 One Stable Layer At A Time

Execution order matters.

Always:

* stabilize one subsystem
* validate it
* complete it
* then move forward

Avoid:

* simultaneous subsystem rewrites
* giant autonomous execution
* unstable architecture layering

---

# 17. Final Principle

This project is NOT:

* a generic chatbot
* a simple dashboard SaaS
* a CRUD CRM platform

This project IS:

* realtime conversational infrastructure
* multilingual voice orchestration
* interruption-aware conversational runtime
* configurable conversational agent platform
* India-first realtime AI communication system

The true moat is:

* realtime orchestration
* interruption quality
* multilingual realism
* conversational timing
* low latency
* workflow reliability
* configurable conversational behavior
* rapid conversational iteration
