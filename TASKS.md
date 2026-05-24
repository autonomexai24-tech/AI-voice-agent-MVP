# TASKS.md

This document tracks active implementation priorities for the AI Receptionist Platform.

This file should remain:
- practical
- execution-focused
- implementation-oriented

This is NOT:
- long-term vision
- architecture philosophy
- brainstorming
- feature dumping

Only include:
- active engineering tasks
- production priorities
- current blockers
- execution-critical improvements

---

# Current Product State

The platform already supports:
- realtime calls
- LiveKit orchestration
- multilingual conversations
- GPT responses
- Sarvam STT/TTS
- deterministic booking extraction
- PostgreSQL persistence
- frontend dashboard
- settings management
- transcript storage

The current focus is:
- runtime intelligence quality
- hallucination reduction
- conversational realism
- frontend/runtime synchronization
- latency optimization

---

# Highest Priority Tasks

## 1. Frontend Runtime Synchronization

### Problem
Frontend settings save correctly into PostgreSQL.

Worker runtime still loads:
- `.env`
- static config

Frontend changes do NOT affect live calls.

---

### Goal
Make PostgreSQL the runtime source of truth.

---

### Required Work
- remove business intelligence from `.env`
- load business settings dynamically per call
- inject DB settings into runtime context
- sync frontend settings into worker lifecycle
- support live business updates without redeploy

---

### Expected Result
Frontend settings immediately affect live calls.

---

# 2. Dynamic Prompt Recomposition

### Problem
System prompt is composed once at call start.

This causes:
- stale context
- hallucinations
- weak long-call grounding
- prompt bloat

---

### Goal
Move to dynamic runtime context injection.

---

### Required Work
- recompute runtime context per-turn
- inject language state dynamically
- inject booking memory dynamically
- inject escalation state dynamically
- inject relevant FAQs only
- reduce overall prompt size

---

### Expected Result
Lower hallucination rates and faster responses.

---

# 3. Runtime Governance Architecture

### Problem
GPT still controls too much conversational behavior.

Current issues:
- workflow drift
- inconsistent responses
- unstable orchestration
- GPT overthinking

---

### Goal
Move operational control into runtime orchestration.

---

### Runtime Should Control
- booking stages
- confirmations
- retries
- escalation
- language state
- business validation
- FAQ retrieval

---

### GPT Should Control
- phrasing
- conversational realism
- tone
- natural multilingual speech

---

### Expected Result
More stable and reliable receptionist behavior.

---

# 4. Booking Workflow Integration

### Problem
Deterministic booking workflow exists but is partially disconnected from spoken output.

Current issue:
- workflow state updates correctly
- GPT sometimes bypasses deterministic prompts

---

### Goal
Ensure deterministic booking responses become spoken responses.

---

### Required Work
- prioritize workflow-generated replies
- inject deterministic prompts into live output
- connect booking workflow to final speech generation
- reconnect Cal.com booking integration cleanly

---

### Expected Result
Reliable and predictable booking conversations.

---

# 5. Dynamic FAQ Retrieval

### Problem
All FAQs are injected into prompts together.

This causes:
- prompt bloat
- hallucination risk
- slower responses
- weaker grounding

---

### Goal
Inject only relevant FAQs during conversations.

---

### Required Work
- classify caller intent
- retrieve matching FAQ subset
- inject only relevant business context
- avoid giant business prompt dumps

---

### Expected Result
Smaller prompts and more accurate responses.

---

# 6. Multilingual Runtime Improvements

### Problem
Language detection works.

However:
- TTS language remains static
- spoken language switching is incomplete
- transitions can feel unnatural

---

### Goal
Support natural multilingual voice adaptation.

---

### Required Work
- improve dominant language tracking
- support mixed-language memory
- improve gradual language blending
- redesign TTS switching strategy
- avoid aggressive language changes

---

### Expected Result
More human multilingual conversations.

---

# 7. Latency Optimization

### Problem
Current responses feel slower than natural conversation.

Root causes:
- large prompts
- GPT over-reasoning
- serial response flow
- excessive conversational complexity

---

### Goal
Reduce perceived latency dramatically.

---

### Required Work
- reduce prompt size
- use deterministic acknowledgments
- shorten AI responses
- optimize response timing
- improve interruption recovery
- reduce unnecessary GPT work

---

### Expected Result
Faster and more natural realtime behavior.

---

# Medium Priority Tasks

## 8. Human Escalation Flow

### Goal
Support seamless human takeover.

---

### Required Work
- escalation triggers
- escalation routing
- transfer handling
- escalation transcripts
- receptionist notifications

---

### Expected Result
Safer production deployment.

---

# 9. Frontend UX Redesign

### Goal
Transform frontend into receptionist training software.

---

### Current Problem
Frontend behaves like:
- AI settings dashboard

---

### Future Direction
Frontend should feel like:
- receptionist training interface

---

### Required Work
- improve conversational language
- remove technical terminology
- simplify onboarding
- improve business setup flow
- improve language configuration UX

---

### Expected Result
Non-technical businesses can configure AI naturally.

---

# 10. Runtime Persistence Improvements

### Problem
Booking/session memory is mostly in-memory.

Worker crashes can lose runtime state.

---

### Goal
Improve runtime resilience.

---

### Required Work
- persist critical booking state
- add lightweight session recovery
- improve transcript synchronization
- improve call lifecycle persistence

---

### Expected Result
Improved reliability during failures.

---

# Lower Priority Tasks

## 11. Analytics
Future:
- call metrics
- latency metrics
- booking conversion metrics

Not current priority.

---

# 12. Enterprise Scaling
Future:
- higher concurrency
- autoscaling
- multi-region infrastructure

Not required for current scale target.

---

# 13. Advanced Voice Personalities
Future:
- premium receptionist styles
- emotional tuning
- business-specific personalities

Not current priority.

---

# Current Known Architecture Problems

## Problem 1
Frontend settings do not control live calls.

---

## Problem 2
Worker runtime depends on `.env` business configuration.

---

## Problem 3
Static prompt injection causes context drift.

---

## Problem 4
GPT still owns too much orchestration behavior.

---

## Problem 5
Deterministic booking responses are partially bypassed.

---

## Problem 6
TTS language switching is incomplete.

---

# Current Success Criteria

The system succeeds when:
- callers feel conversations are human
- hallucinations become rare
- frontend settings control live behavior
- responses feel fast
- multilingual flow feels natural
- booking conversations remain reliable

---

# Engineering Priorities

Prioritize:
1. runtime correctness
2. conversational realism
3. hallucination reduction
4. low latency
5. operational simplicity

Avoid:
- unnecessary complexity
- overengineered AI systems
- excessive frameworks
- autonomous agent architectures

---

# Repository Workflow Philosophy

This repository is optimized for:
- AI-assisted development
- context engineering
- fast iteration
- small focused changes

Implementation should remain:
- modular
- readable
- deterministic where possible
- realtime-safe

---

# Current Build Direction

The platform is evolving from:

```text
Prompt-heavy voice bot