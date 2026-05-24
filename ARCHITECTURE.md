# ARCHITECTURE.md

This document defines the technical architecture of the AI Receptionist Platform.

This file describes:
- realtime runtime architecture
- orchestration design
- conversational flow
- persistence systems
- multilingual behavior
- booking systems
- runtime governance
- deployment structure

This document is NOT:
- product vision
- feature planning
- implementation task tracking

The purpose of this document is:
- architecture clarity
- runtime truth
- system boundaries
- engineering alignment

---

# Core System Philosophy

The platform is NOT:
- a chatbot
- a sequential API workflow
- a prompt-only AI system
- a CRUD backend

The platform IS:
- a realtime conversational runtime
- an event-driven orchestration engine
- a multilingual voice system
- a deterministic operational layer

The architecture prioritizes:
- low latency
- conversational realism
- interruption quality
- runtime reliability
- hallucination resistance

---

# High-Level Architecture

```text
Caller
   ↓
VoIP / SIP Provider
   ↓
LiveKit Cloud
   ↓
Realtime Worker Runtime
   ↓
Runtime Orchestration Layer
   ↓
OpenAI + Deterministic Systems
   ↓
Sarvam TTS
   ↓
Caller