# Realtime Orchestration Skill

---

# 1. Purpose

This skill defines how the realtime orchestration engine should behave inside the AI Voice Receptionist system.

This is the CORE runtime architecture of the entire platform.

The orchestration layer coordinates:
- audio streaming
- STT
- TTS
- OpenAI
- interruptions
- memory
- playback
- language switching
- session state

This layer is the actual "Vapi replacement."

---

# 2. Core Philosophy

The system is NOT:
- request-response architecture
- CRUD backend
- sequential API workflow

The system IS:
- realtime event-driven runtime
- continuous streaming engine
- conversational state machine

Everything should optimize for:
- responsiveness
- interruption quality
- human conversational realism

---

# 3. Realtime Runtime Philosophy

The runtime continuously operates like:

```txt
listen
↓
transcribe
↓
reason
↓
speak
↓
interrupt
↓
resume