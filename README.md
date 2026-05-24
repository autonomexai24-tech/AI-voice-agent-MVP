# AI Receptionist Platform

Human-like realtime multilingual AI receptionist platform built using:
- LiveKit
- FastAPI
- OpenAI
- Sarvam AI
- PostgreSQL
- Next.js

This system is designed to behave like a real receptionist — not a chatbot.

The platform focuses on:
- realtime responsiveness
- natural voice conversation
- multilingual speech blending
- low hallucination architecture
- deterministic booking workflows
- runtime context grounding
- operational simplicity

---

# Vision

Build a generic AI receptionist platform for businesses.

Examples:
- clinics
- salons
- gyms
- restaurants
- real estate offices
- law firms
- local service businesses

The AI should:
- answer calls naturally
- speak like a trained receptionist
- handle interruptions smoothly
- switch languages naturally
- collect booking details reliably
- escalate to humans when needed

The goal is NOT:
- robotic chatbot behavior
- long assistant-style responses
- over-intelligent autonomous agents

The goal IS:
- believable receptionist behavior
- operational reliability
- fast conversational flow
- human-like speaking rhythm

Success metric:

> “The receptionist sounded real.”

---

# Product Principles

## 1. Responsiveness First

Perceived speed matters more than perfect intelligence.

The caller should feel:
- immediate acknowledgment
- low silence
- fast turn-taking
- natural interruptions
- smooth conversation flow

Even small delays reduce realism.

Realtime responsiveness is a core architecture priority.

---

## 2. Hybrid Intelligence Architecture

The platform uses a hybrid system.

### AI Handles
- natural conversation
- multilingual behavior
- personality
- tone
- conversational flexibility

### Deterministic Systems Handle
- booking workflows
- validation
- confirmations
- escalation rules
- business logic
- persistence
- state tracking

This reduces:
- hallucinations
- instability
- unpredictable behavior
- operational risk

---

## 3. Runtime Context Grounding

The AI should never rely only on static prompts.

The runtime should dynamically inject:
- business settings
- active language
- booking memory
- FAQ context
- conversation stage
- escalation state

This is the primary hallucination prevention strategy.

---

## 4. Natural Multilingual Conversation

Supported behaviors:
- Hindi
- English
- Kannada
- Telugu
- Marathi
- Hinglish
- mixed-language conversation

The AI should:
- begin in the default business language
- detect caller comfort level
- gradually blend languages naturally
- avoid aggressive switching

Example:
- business default = Kannada
- caller uses English words
- AI slowly mixes English naturally

This feels more human.

---

## 5. Human Escalation Always Exists

AI should not attempt to solve everything.

Escalation triggers:
- caller frustration
- repeated misunderstandings
- unsupported requests
- low confidence situations
- emergency cases

AI is an operational layer — not a replacement for human judgment.

---

# Current Architecture

```text
Caller
   ↓
LiveKit SIP
   ↓
AgentSession
   ↓
Sarvam STT
   ↓
Runtime Context Engine
   ↓
Prompt Orchestrator
   ↓
OpenAI
   ↓
Sarvam TTS
   ↓
Caller