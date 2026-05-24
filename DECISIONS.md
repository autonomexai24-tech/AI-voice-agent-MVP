# DECISIONS.md

This document records permanent architectural and product decisions for the AI Receptionist Platform.

This file exists to:
- prevent architectural confusion
- preserve engineering reasoning
- avoid repeated debates
- guide AI-assisted development
- maintain long-term consistency

This document should contain:
- finalized decisions
- architecture commitments
- rejected directions
- operational philosophy

This file should NOT contain:
- temporary tasks
- implementation details
- sprint planning
- speculative ideas

Only record:
- high-confidence architectural decisions

---

# Core Product Decision

## Decision
The platform is a:

# Generic AI Receptionist Platform

NOT:
- clinic-only software
- chatbot platform
- autonomous AI agent system

The system should support:
- clinics
- salons
- gyms
- restaurants
- local businesses
- service businesses

---

# Primary Product Goal

## Decision
The platform optimizes for:

> “That sounded like a real receptionist.”

NOT:
- maximum intelligence
- long AI conversations
- fully autonomous behavior
- assistant-style interactions

Primary priorities:
- realism
- responsiveness
- operational reliability
- low hallucination behavior

---

# Realtime Runtime Architecture

## Decision
The platform will remain:
- realtime
- streaming-based
- event-driven
- interruption-sensitive

The runtime architecture will continue using:
- LiveKit AgentSession
- async orchestration
- streaming STT/TTS
- realtime conversational flow

---

# LiveKit Decision

## Decision
LiveKit remains the core realtime infrastructure layer.

LiveKit responsibilities:
- audio transport
- session management
- interruption handling
- realtime streaming

LiveKit is NOT:
- orchestration logic
- booking logic
- conversational intelligence

---

## Rejected Direction
Do NOT:
- replace LiveKit with custom websocket orchestration
- build custom audio transport layers
- move toward polling architectures

Current LiveKit direction is correct.

---

# GPT Responsibility Decision

## Decision
GPT should ONLY control:
- conversational phrasing
- tone
- multilingual flexibility
- natural speaking behavior

GPT should NOT control:
- workflows
- validation
- escalation
- operational state
- deterministic business logic

---

# Runtime Governance Decision

## Decision
Operational intelligence belongs in the runtime.

The runtime controls:
- booking stages
- retries
- confirmations
- escalation
- language state
- FAQ retrieval
- business validation
- interruption state

This is a permanent architectural direction.

---

# Hybrid Architecture Decision

## Decision
The platform intentionally uses:
- deterministic systems
- GPT conversation generation

NOT:
- fully autonomous AI orchestration

This hybrid architecture reduces:
- hallucinations
- instability
- latency
- operational risk
- infrastructure cost

---

# Frontend Philosophy Decision

## Decision
The frontend is:

# Receptionist Training Software

NOT:
- prompt engineering software
- AI configuration panel
- developer dashboard

Frontend UX should remain:
- non-technical
- business-focused
- operationally simple

---

# Frontend Runtime Authority Decision

## Decision
Frontend business settings must become:
- runtime source of truth
- dynamically loaded by worker runtime
- stored in PostgreSQL

`.env` files should only contain:
- secrets
- infrastructure config
- provider credentials

Business intelligence must NOT remain inside `.env`.

---

# PostgreSQL Decision

## Decision
PostgreSQL is:
- persistence layer
- business memory layer
- runtime configuration layer

PostgreSQL should store:
- business settings
- services
- FAQs
- escalation rules
- receptionist behavior
- transcripts
- booking state

---

# Prompt Architecture Decision

## Decision
The system should avoid:
- giant prompts
- static prompt dumps
- large FAQ injection
- prompt-centric orchestration

Future architecture should use:
- dynamic prompt recomposition
- runtime context injection
- lightweight retrieval
- minimal prompt surfaces

---

# Hallucination Strategy Decision

## Decision
Hallucination prevention should rely on:
- runtime grounding
- deterministic workflows
- retrieval systems
- validation
- escalation safety

NOT:
- giant prompt instructions
- excessive guardrails
- prompt-only enforcement

---

# Booking Architecture Decision

## Decision
Booking workflows remain:
- deterministic
- state-driven
- runtime-controlled

GPT should NOT fully manage booking logic.

The runtime owns:
- extraction
- confirmations
- retries
- correction handling
- validation
- escalation

---

# Multilingual Decision

## Decision
The system supports:
- English
- Hindi
- Kannada
- Telugu
- Marathi
- Hinglish
- mixed-language conversations

The AI should:
- gradually adapt language
- blend naturally
- mirror caller style
- avoid aggressive switching

Natural language blending is intentional.

---

# TTS Strategy Decision

## Decision
Future architecture should support:
- dynamic TTS language behavior
- multilingual speech adaptation
- natural transitions

Current static TTS locking is considered temporary technical debt.

---

# Latency Philosophy Decision

## Decision
The platform optimizes for:

# Perceived responsiveness

NOT:
- maximum AI reasoning depth

Primary latency reduction strategies:
- smaller prompts
- deterministic acknowledgments
- reduced GPT reasoning
- lightweight orchestration
- runtime grounding

---

# Cost Optimization Decision

## Decision
The platform should remain:
- lightweight
- cheap
- operationally simple

Avoid:
- unnecessary GPU infrastructure
- excessive vector systems
- autonomous agent swarms
- overengineered AI stacks

The platform should scale through:
- orchestration quality
- runtime efficiency
- deterministic systems

NOT:
- larger models
- larger infrastructure

---

# Infrastructure Decision

## Decision
Current deployment strategy remains:
- EasyPanel
- Docker containers
- PostgreSQL
- LiveKit Cloud

Current target:
- ~5 concurrent calls

Kubernetes is NOT required at current scale.

---

# Human Escalation Decision

## Decision
Human takeover remains mandatory.

AI should escalate during:
- low confidence
- unsupported requests
- caller frustration
- operational uncertainty
- emergency situations

The platform does NOT pursue:
- fully autonomous receptionist behavior

---

# AsyncIO Decision

## Decision
All realtime systems remain:
- asynchronous
- streaming-oriented
- non-blocking

Critical realtime systems:
- LiveKit
- STT
- TTS
- GPT streaming
- playback
- interruption handling

Blocking operations inside runtime are prohibited.

---

# Frontend Settings Problem Decision

## Decision
Current frontend/runtime disconnect is acknowledged technical debt.

Current reality:

```text id="gv67uy"
Frontend
↓
PostgreSQL
↓
NOT used by runtime