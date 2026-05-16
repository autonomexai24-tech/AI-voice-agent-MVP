# AI Voice Receptionist Platform — PLAN.md

---

# 1. Product Vision

Build a realtime multilingual AI conversational platform capable of handling natural human phone conversations with ultra-low latency, interruption awareness, business workflow execution, and configurable conversational behavior.

The system should feel indistinguishable from a highly trained human receptionist.

The long-term vision is NOT to build a simple chatbot or CRM dashboard.

The long-term vision is to build:

```txt
Realtime Conversational Infrastructure
```

optimized for:

* human-like voice conversations
* multilingual Indian language support
* realtime interruption handling
* business workflow automation
* configurable AI agent behavior
* rapid conversational iteration
* low-latency orchestration
* scalable voice operations

---

# 2. Core Product Direction

The platform is designed as:

```txt
Config-Driven Conversational Agent Infrastructure
```

The first production role is:

```txt
AI Receptionist
```

Future roles may include:

* AI sales agent
* AI support agent
* AI lead qualification agent
* AI appointment coordinator
* AI customer success agent
* AI hospitality assistant
* AI commerce assistant

The architecture must support future role expansion WITHOUT major rewrites.

---

# 3. Primary MVP Goal

The MVP goal is:

```txt
Reliable AI appointment booking through realtime phone conversations.
```

The MVP must:

* answer inbound calls naturally
* speak in local Indian languages
* auto-detect language dynamically
* follow business-specific instructions
* handle interruptions naturally
* book appointments reliably
* log calls and transcripts
* provide CRM visibility
* allow live prompt editing

The system should prioritize:

* conversational realism
* reliability
* low latency
* appointment completion
* fast iteration speed

---

# 4. Product Principles

## 4.1 Human-Like Conversation First

The system must prioritize:

* natural pacing
* conversational timing
* interruption handling
* fast acknowledgment
* natural pauses
* human conversational rhythm

Raw AI intelligence is secondary to conversational realism.

---

## 4.2 Realtime Architecture First

The backend is NOT a CRUD backend.

The backend is:

* a realtime orchestration engine
* a streaming runtime
* a conversational state system
* an event-driven audio pipeline

Realtime orchestration quality is the core product moat.

---

## 4.3 India-First Optimization

Primary optimization targets:

* Hindi
* Kannada
* Tamil
* Telugu
* Malayalam
* Marathi
* Punjabi
* Bengali
* Hinglish
* code-mixed conversations

The system must work well with:

* Indian telecom audio
* Indian accents
* noisy environments
* multilingual switching

---

## 4.4 Low Latency First

Target:

```txt
Perceived latency under 1 second.
```

The AI should:

* acknowledge quickly
* respond naturally
* avoid awkward silence
* stream responses rapidly

Perceived responsiveness matters more than benchmark latency.

---

## 4.5 Fast Iteration Velocity

The architecture must optimize for:

* fast improvements
* safe iteration
* modular prompts
* configurable workflows
* provider flexibility
* rapid conversational tuning

Future improvements should NOT require large rewrites.

---

## 4.6 Modularity

Providers must remain replaceable.

Examples:

* Sarvam → replaceable later
* OpenAI → replaceable later
* LiveKit Cloud → self-hosted later
* VoBiz → replaceable later

Business behavior must remain configuration-driven.

Avoid hardcoded workflows.

---

# 5. Current System Status

## 5.1 Completed Infrastructure

The following infrastructure is already functioning:

### Telephony

* VoBiz SIP integration
* LiveKit SIP ingress
* dynamic room creation
* worker dispatch
* realtime room orchestration

### Voice Runtime

* realtime audio streaming
* LiveKit AgentSession
* realtime audio playback
* interruption-capable architecture

### Speech Systems

* Sarvam STT integration
* Sarvam TTS integration
* realtime transcription
* realtime speech playback
* multilingual audio support

### AI Runtime

* OpenAI GPT-4o-mini integration
* realtime response generation
* business prompt injection
* receptionist behavior

### Observability

* structured logging
* session lifecycle logging
* provider diagnostics
* transcript events
* playback events

---

## 5.2 Completed Phase Summary

### Phase 0 — Infrastructure Bring-Up

Status: COMPLETE

Completed:

* SIP routing
* LiveKit connectivity
* worker orchestration
* dispatch lifecycle

---

### Phase 1A — Audio Connectivity

Status: COMPLETE

Completed:

* realtime audio subscription
* audio transport
* audio publication

---

### Phase 1B — Realtime STT

Status: COMPLETE

Completed:

* Sarvam websocket streaming
* realtime transcripts
* speech boundary detection

---

### Phase 1C — OpenAI Conversation Loop

Status: COMPLETE

Completed:

* transcript → LLM pipeline
* business prompt injection
* realtime AI responses

---

### Phase 1D — Realtime TTS + Playback

Status: COMPLETE

Completed:

* TTS generation
* audio playback
* realtime speech streaming
* caller playback delivery

---

# 6. Current MVP Scope

The MVP now expands beyond infrastructure validation.

The system now includes:

* frontend dashboard
* CRM
* appointment orchestration
* call logs
* recordings
* configurable prompts
* business configuration
* booking workflows

The MVP remains:

* single-business initially
* desktop-first
* local-first development
* single-calendar initially
* no auth initially

The MVP intentionally avoids:

* enterprise scaling
* Kubernetes
* multi-region deployment
* multi-tenant billing
* large analytics systems

---

# 7. MVP Frontend Architecture

Frontend stack:

```txt
Next.js + TypeScript
```

Frontend purpose:

* operational control
* prompt management
* CRM visibility
* booking visibility
* conversation monitoring

The frontend is NOT the core product.

The realtime conversational engine remains the primary moat.

---

# 8. MVP Frontend Pages

## 8.1 Dashboard

Purpose:

* operational visibility
* business metrics
* booking conversion tracking
* call activity visibility

Initial metrics:

* total calls
* answered calls
* missed calls
* successful bookings
* booking conversion rate
* average call duration
* human transfer rate

---

## 8.2 CRM + Calendar

Purpose:

* contact management
* appointment visibility
* transcript access
* recording access
* booking tracking

Initial entities:

* contacts
* call history
* appointment history
* transcripts
* recordings
* missed calls

Initial booking provider:

```txt
Cal.com
```

The AI should:

* fetch available slots
* create bookings automatically
* confirm bookings conversationally

Single calendar only initially.

---

## 8.3 Agent Settings

Purpose:

* live conversational configuration
* prompt management
* business rule management
* language configuration

Editable settings:

* greetings
* tone
* business prompts
* refusal policies
* booking instructions
* escalation rules
* default language
* ambience settings

Prompt changes should apply:

```txt
New calls only
```

without restarting the runtime.

---

## 8.4 Call Logs

Purpose:

* transcript visibility
* recording playback
* operational debugging
* booking review

Each call log should include:

* transcript
* recording playback
* booking outcome
* language used
* call duration
* AI response history
* interruption metadata

---

# 9. Backend Architecture

## 9.1 Core Runtime

Primary runtime:

```txt
Python Realtime Voice Runtime
```

Responsibilities:

* SIP orchestration
* realtime audio handling
* conversation runtime
* STT/TTS orchestration
* interruption handling
* playback streaming
* conversation state management

---

## 9.2 API Layer

Backend APIs:

```txt
FastAPI
```

Responsibilities:

* frontend APIs
* CRM APIs
* transcript APIs
* recordings APIs
* settings APIs
* analytics APIs
* Cal.com orchestration

---

## 9.3 Frontend

Frontend:

```txt
Next.js + TypeScript
```

Responsibilities:

* dashboard
* CRM
* settings
* call logs
* operational control

---

## 9.4 Database

Database:

```txt
PostgreSQL
```

Responsibilities:

* contacts
* calls
* transcripts
* recordings metadata
* appointments
* prompt configuration
* business configuration
* analytics

---

# 10. Realtime Voice Architecture

```txt
Caller
↓
VoBiz SIP
↓
LiveKit Cloud
↓
Python Voice Runtime
↓
Sarvam STT
↓
Conversation Engine
↓
OpenAI
↓
Sarvam TTS
↓
Realtime Audio Playback
↓
Caller
```

---

# 11. Prompt Architecture

The system must use:

```txt
Modular Prompt Architecture
```

NOT giant monolithic prompts.

Prompt layers should include:

* core identity
* tone
* business rules
* refusal policies
* booking instructions
* multilingual rules
* escalation rules
* conversation behavior

All prompts should remain:

* configurable
* editable
* modular
* database-driven later

Avoid hardcoded business behavior.

---

# 12. Conversation State System

The conversation runtime should evolve toward:

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

The runtime should avoid uncontrolled conversational flows.

---

# 13. Appointment Workflow Engine

Primary MVP KPI:

```txt
Reliable appointment booking.
```

Initial booking flow:

* identify customer intent
* collect booking details
* fetch availability from Cal.com
* confirm available slot
* create booking automatically
* confirm booking conversationally

Required booking fields:

* customer name
* phone number
* appointment date
* appointment time
* service type
* doctor/staff
* notes

Initial scope:

* one booking per call
* single calendar
* automatic booking confirmation

---

# 14. Multilingual System

The system must support:

* automatic language detection
* realtime language switching
* code-mixed conversations
* configurable default language

Example:

* business default = Kannada
* caller speaks Hindi
* AI switches naturally to Hindi

The AI should always prioritize:

* caller comfort
* natural communication
* conversational continuity

---

# 15. Human Transfer System

Initial escalation support:

* human transfer requests
* booking disputes
* escalation conditions

Example:

```txt
"Please hold while I transfer your call."
```

Full call-center transfer orchestration is deferred.

---

# 16. Recording System

Initial storage:

```txt
Local filesystem
```

Future migration path:

* S3
* Cloudflare R2
* external object storage

Recordings should support:

* playback
* transcript review
* operational debugging

---

# 17. Deployment Strategy

## MVP Development

Primary mode:

```txt
Native local development first
```

Goals:

* fastest iteration
* low complexity
* easier debugging

---

## Future Deployment

Planned deployment:

```txt
Docker + EasyPanel
```

Future hosting goals:

* containerized deployment
* reproducible runtime
* scalable orchestration

---

# 18. Remaining Major Systems

The following systems remain incomplete:

## Prompt Architecture System

* modular prompts
* prompt editor
* prompt orchestration

## Conversation State Machine

* structured flows
* interruption recovery
* booking transitions

## Booking Workflow Engine

* Cal.com orchestration
* slot validation
* booking confirmations

## Persistence Layer

* Postgres integration
* CRM persistence
* transcript storage

## Frontend MVP

* dashboard
* CRM
* settings
* call logs

## Multilingual Intelligence

* language switching refinement
* multilingual prompts
* language-aware responses

## Humanization Layer

* conversational pacing
* fillers
* acknowledgment behavior
* ambience realism

---

# 19. Future Expansion

Future roadmap includes:

* multi-tenant SaaS
* authentication
* role-based access
* advanced analytics
* AI summaries
* multilingual memory
* advanced CRM
* sales agents
* support agents
* outbound calling
* campaign orchestration
* self-hosted media infrastructure

These are intentionally deferred.

---

# 20. Success Definition

The MVP succeeds when:

A real customer can:

* call a business number
* speak naturally in local language
* book an appointment successfully
* interrupt naturally
* receive human-like responses
* experience low latency
* feel like they spoke to a human receptionist

without realizing the system is AI.

---

# 21. Final Principle

This project is NOT:

* a simple chatbot
* a dashboard wrapper
* a generic SaaS CRM

This project IS:

* a realtime conversational infrastructure platform
* optimized for multilingual human-like voice interaction
* focused on rapid conversational improvement
* optimized for India-first realtime voice experiences

The true moat is:

* realtime orchestration
* interruption quality
* multilingual realism
* conversational timing
* low latency
* rapid iteration capability
* configurable conversational intelligence
* reliable workflow execution
The backend must remain self-initializing.

Database schema creation and startup validation
must occur INSIDE application startup lifecycle,
not through external deployment scripts only.

The application must:
- verify DB connectivity
- initialize schema safely
- validate required tables
- fail loudly if persistence unavailable
- expose startup health state

The runtime should never become partially alive.