# OpenAI Conversation Skill

---

# 1. Purpose

This skill defines how OpenAI should be used inside the realtime AI voice receptionist system.

OpenAI is the conversational intelligence layer.

Responsibilities:
- understanding customer intent
- generating conversational replies
- following business prompts
- maintaining conversation memory
- handling refusals
- producing human-like responses

OpenAI is NOT:
- the audio transport system
- the STT system
- the TTS system
- the realtime streaming infrastructure

It is the reasoning and conversation engine.

---

# 2. Core Role In Architecture

```txt
Customer Speech
↓
STT Transcript
↓
Conversation Engine
↓
OpenAI
↓
AI Response
↓
TTS
↓
Customer