# Python AsyncIO Skill

---

# 1. Purpose

This skill defines how Python AsyncIO should be used inside the realtime AI voice receptionist system.

This project is:
- realtime
- streaming-based
- event-driven
- interruption-sensitive

Because of this, AsyncIO is a core foundation of the entire runtime system.

Incorrect async architecture will cause:
- latency spikes
- frozen playback
- broken interruptions
- delayed responses
- unstable audio pipelines

---

# 2. Core Philosophy

The system must behave like:
- a continuous realtime engine

NOT:
- a traditional request-response web application

All realtime operations should remain:
- asynchronous
- non-blocking
- streaming-oriented

---

# 3. AsyncIO Role In Architecture

AsyncIO coordinates:
- LiveKit audio streaming
- STT streaming
- OpenAI streaming
- TTS streaming
- playback streaming
- interruption handling
- session state management

All of these systems run concurrently.

---

# 4. Core Async Principles

## 4.1 Never Block The Event Loop

Avoid:
- long synchronous operations
- blocking sleep calls
- heavy CPU tasks inline
- synchronous API requests

Blocking the event loop causes:
- audio freezes
- delayed interruptions
- broken realtime behavior

---

## 4.2 Everything Realtime Must Be Async

Examples:
- audio streaming
- websocket communication
- provider requests
- playback pipelines
- interruption handling

All should remain async-first.

---

## 4.3 Streaming Over Waiting

Never:
- wait for complete responses
- wait for complete audio generation
- block until processing finishes

Prefer:
- incremental streaming
- chunk-based processing
- concurrent pipelines

---

# 5. Core Runtime Philosophy

The runtime behaves like:

```txt
listen
↓
stream
↓
reason
↓
speak
↓
interrupt
↓
resume