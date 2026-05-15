# LiveKit Realtime Skill

---

# 1. Purpose

This skill defines how LiveKit should be used inside the AI Voice Receptionist system.

LiveKit is the realtime audio infrastructure layer.

It is responsible for:
- realtime audio transport
- voice streaming
- room management
- participant communication
- low-latency audio delivery

LiveKit is NOT:
- the AI brain
- the STT engine
- the TTS engine
- the conversation engine

It is the realtime audio highway.

---

# 2. Core Responsibility

LiveKit handles:

```txt
Caller Audio
↔
Realtime Audio Stream
↔
Python Voice Agent